from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


_LOGGER = logging.getLogger("capswriter.tsf.revisions")


@dataclass(frozen=True, slots=True)
class RevisionStreamStats:
    submitted: int
    dispatched: int
    coalesced: int
    failures: int


class LatestTextRevisionCoalescer:
    """Dispatch only the latest full-text revision with bounded latency.

    There is at most one callback awaiting an ACK and one pending text value.
    Revisions arriving while the callback is busy replace that pending value.
    This deliberately lives above the named-pipe request layer: dropping a
    request after its ACK future has been registered would violate the request
    contract and make the caller wait for a response that can never arrive.
    """

    def __init__(
        self,
        callback: Callable[[str], Awaitable[None]],
        *,
        min_interval_s: float = 0.05,
        label: str = "polish",
    ) -> None:
        self._callback = callback
        self._min_interval_s = max(0.0, min_interval_s)
        self._label = label
        self._pending: str | None = None
        self._last_dispatched: str | None = None
        self._last_started_at: float | None = None
        self._worker: asyncio.Task[None] | None = None
        self._flush_event = asyncio.Event()
        self._closed = False
        self._submitted = 0
        self._dispatched = 0
        self._coalesced = 0
        self._failures = 0

    @property
    def stats(self) -> RevisionStreamStats:
        return RevisionStreamStats(
            submitted=self._submitted,
            dispatched=self._dispatched,
            coalesced=self._coalesced,
            failures=self._failures,
        )

    @property
    def is_idle(self) -> bool:
        return self._pending is None and (self._worker is None or self._worker.done())

    def submit(self, text: str) -> None:
        """Offer a cumulative text revision without waiting for its ACK."""
        if self._closed:
            return
        if text == self._pending or (
            self._pending is None and text == self._last_dispatched
        ):
            return
        self._submitted += 1
        if self._pending is not None:
            self._coalesced += 1
        self._pending = text
        self._ensure_worker()

    def _ensure_worker(self) -> None:
        if self._worker is None or self._worker.done():
            self._worker = asyncio.create_task(
                self._run(),
                name=f"tsf_revision_coalescer:{self._label}",
            )

    async def _wait_for_send_slot(self) -> None:
        if self._last_started_at is None or self._min_interval_s <= 0:
            return
        loop = asyncio.get_running_loop()
        delay = self._min_interval_s - (loop.time() - self._last_started_at)
        if delay <= 0:
            return
        try:
            await asyncio.wait_for(self._flush_event.wait(), timeout=delay)
        except TimeoutError:
            pass
        finally:
            self._flush_event.clear()

    async def _run(self) -> None:
        try:
            while self._pending is not None and not self._closed:
                await self._wait_for_send_slot()
                # Use the newest value after the pacing wait, not the value
                # that happened to wake the worker.
                text = self._pending
                self._pending = None
                if text is None:
                    continue
                self._last_started_at = asyncio.get_running_loop().time()
                try:
                    await self._callback(text)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001 - progress is best effort
                    self._failures += 1
                    _LOGGER.warning(
                        "TSF revision dispatch failed stream=%s chars=%d error=%s",
                        self._label,
                        len(text),
                        type(exc).__name__,
                    )
                else:
                    self._dispatched += 1
                    self._last_dispatched = text
        finally:
            self._worker = None
            # No await occurs between the loop condition and this cleanup, but
            # keep this defensive restart for callbacks that synchronously
            # submit another value while completing.
            if self._pending is not None and not self._closed:
                self._ensure_worker()

    async def flush(self) -> None:
        """Dispatch and ACK the latest offered revision before returning."""
        if self._closed:
            return
        while self._pending is not None or self._worker is not None:
            if self._pending is not None:
                self._ensure_worker()
            worker = self._worker
            if worker is None:
                continue
            self._flush_event.set()
            await worker

    async def close(self, *, flush: bool = True) -> None:
        if self._closed:
            return
        if flush:
            await self.flush()
        self._closed = True
        self._pending = None
        worker = self._worker
        if worker is not None and not worker.done():
            worker.cancel()
            try:
                await worker
            except asyncio.CancelledError:
                pass
        self._worker = None
        stats = self.stats
        if stats.submitted:
            _LOGGER.info(
                "TSF revision stream closed stream=%s submitted=%d "
                "dispatched=%d coalesced=%d failures=%d",
                self._label,
                stats.submitted,
                stats.dispatched,
                stats.coalesced,
                stats.failures,
            )

    async def cancel(self) -> None:
        """Discard pending work and stop an in-flight dispatch."""
        await self.close(flush=False)
