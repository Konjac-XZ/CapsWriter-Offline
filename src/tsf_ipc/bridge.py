from __future__ import annotations

import asyncio
import hashlib
import logging
import platform
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Protocol

from src.infra.config import config as Config

from .host_processor import (
    DEFAULT_HOST_PROCESSOR_REGISTRY,
    HostApplication,
    HostProcessor,
    HostProcessorRegistry,
    RevisionDecision,
)
from .protocol import CompositionStyle, Frame, Operation, Status
from .windows_pipe import BrokerReply, WindowsNamedPipeBroker


_LOGGER = logging.getLogger("capswriter.tsf.bridge")


class SpeechTipBroker(Protocol):
    @property
    def startup_error(self) -> Exception | None: ...

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> bool: ...

    def stop(self) -> None: ...

    async def request(self, frame: Frame, timeout: float) -> BrokerReply | None: ...

    def broadcast(self, frame: Frame) -> int: ...

    def set_event_handler(self, handler: Callable[[Frame], None] | None) -> None: ...


@dataclass(slots=True)
class _CompositionState:
    task_id: str
    session_id: uuid.UUID
    revision: int
    captured: bool
    style: CompositionStyle
    host: HostApplication
    processor: HostProcessor
    defer_final: bool = False


class TsfSpeechTipBridge:
    def __init__(
        self,
        broker: SpeechTipBroker | None = None,
        processor_registry: HostProcessorRegistry | None = None,
    ) -> None:
        self._broker = broker or WindowsNamedPipeBroker()
        self._processor_registry = processor_registry or DEFAULT_HOST_PROCESSOR_REGISTRY
        self._state: _CompositionState | None = None
        self._lock = asyncio.Lock()
        self._rollback_confirmed_tasks: set[str] = set()
        self._rollback_failed_tasks: set[str] = set()
        set_event_handler = getattr(self._broker, "set_event_handler", None)
        if set_event_handler is not None:
            set_event_handler(self._handle_event)

    def _handle_event(self, frame: Frame) -> None:
        if frame.operation != int(Operation.COMPOSITION_TERMINATED):
            return
        state = self._state
        if state is None or state.session_id != frame.session_id:
            return
        state.captured = False
        rollback_trusted = (
            frame.status == int(Status.APPLIED)
            and state.processor.trust_external_termination_rollback
        )
        if rollback_trusted:
            self._rollback_confirmed_tasks.add(state.task_id)
            self._rollback_failed_tasks.discard(state.task_id)
        else:
            self._rollback_failed_tasks.add(state.task_id)
            self._rollback_confirmed_tasks.discard(state.task_id)
        _LOGGER.warning(
            "TSF composition terminated session=%s revision=%d rollback=%s "
            "trusted=%s host=%s processor=%s",
            frame.session_id.hex[:8],
            frame.revision,
            self._status_name(frame.status),
            rollback_trusted,
            state.host.process_name or "unknown",
            state.processor.name,
        )

    def take_confirmed_termination_rollback(self, task_id: str | None) -> bool:
        """Consume proof that an externally terminated composition was erased."""
        if task_id is None or task_id not in self._rollback_confirmed_tasks:
            return False
        self._rollback_confirmed_tasks.remove(task_id)
        if self._state is not None and self._state.task_id == task_id:
            self._state = None
        return True

    def take_failed_termination_rollback(self, task_id: str | None) -> bool:
        """Consume an untrusted or failed external-termination rollback."""
        if task_id is None or task_id not in self._rollback_failed_tasks:
            return False
        self._rollback_failed_tasks.remove(task_id)
        if self._state is not None and self._state.task_id == task_id:
            self._state = None
        return True

    @property
    def enabled(self) -> bool:
        return (
            bool(getattr(Config, "tsf_speech_tip_enabled", False))
            and platform.system() == "Windows"
        )

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> bool:
        return self.enabled and self._broker.start(loop)

    @property
    def startup_error(self) -> Exception | None:
        return self._broker.startup_error

    def stop(self) -> None:
        self._broker.stop()

    def owns_task(self, task_id: str | None) -> bool:
        return bool(
            task_id
            and self._state is not None
            and self._state.task_id == task_id
            and self._state.captured
        )

    @staticmethod
    def _ack_timeout() -> float:
        return max(
            0.01,
            float(getattr(Config, "tsf_speech_tip_ack_timeout_ms", 150)) / 1000.0,
        )

    async def _request_applied(self, frame: Frame) -> BrokerReply | None:
        started = time.monotonic()
        clients_before = getattr(self._broker, "client_count", None)
        try:
            ack = await self._broker.request(frame, self._ack_timeout())
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - IPC diagnostics must not fail ASR
            self._log_request_result(
                frame,
                started,
                "transport_error",
                clients_before,
                error=type(exc).__name__,
            )
            return None
        ack_frame = ack.frame if ack is not None else None
        applied = bool(
            ack_frame
            and ack_frame.is_ack
            and ack_frame.acknowledged_operation == int(frame.operation)
            and ack_frame.session_id == frame.session_id
            and ack_frame.revision == frame.revision
            and ack_frame.status == int(Status.APPLIED)
        )
        if ack is None:
            result = "no_clients" if clients_before == 0 else "timeout_or_disconnect"
        elif ack_frame is None or not ack_frame.is_ack:
            result = "invalid_response"
        else:
            result = self._status_name(ack_frame.status)
        self._log_request_result(frame, started, result, clients_before)
        return ack if applied else None

    @staticmethod
    def _status_name(status: int) -> str:
        try:
            return Status(status).name.lower()
        except ValueError:
            return f"unknown_{status}"

    @staticmethod
    def _operation_name(operation: int) -> str:
        try:
            return Operation(operation).name.lower()
        except ValueError:
            return f"unknown_{operation}"

    def _log_request_result(
        self,
        frame: Frame,
        started: float,
        result: str,
        clients: int | None,
        *,
        error: str | None = None,
    ) -> None:
        text_hash = (
            hashlib.sha256(frame.text.encode("utf-8")).hexdigest()[:8]
            if frame.text
            else "-"
        )
        log = _LOGGER.info if result == Status.APPLIED.name.lower() else _LOGGER.warning
        log(
            "TSF request op=%s session=%s revision=%d style=%d chars=%d "
            "text_hash=%s clients=%s result=%s elapsed_ms=%.1f%s",
            self._operation_name(int(frame.operation)),
            frame.session_id.hex[:8],
            frame.revision,
            int(frame.status),
            len(frame.text),
            text_hash,
            clients if clients is not None else "unknown",
            result,
            (time.monotonic() - started) * 1000.0,
            f" error={error}" if error else "",
        )

    async def begin_or_revise(
        self,
        task_id: str,
        text: str,
        style: CompositionStyle = CompositionStyle.TRANSCRIPTION,
    ) -> bool:
        if not self.enabled:
            return False
        async with self._lock:
            if self._state is not None and self._state.task_id == task_id:
                if not self._state.captured:
                    return False
                if self._state.defer_final:
                    return False
                if (
                    self._state.processor.process_revision(text, style)
                    is RevisionDecision.DEFER_FINAL
                ):
                    return await self._defer_to_final(self._state)
                self._state.revision += 1
                applied = await self._request_applied(
                    Frame(
                        Operation.REVISE,
                        self._state.session_id,
                        self._state.revision,
                        text,
                        status=style,
                    )
                )
                if applied:
                    self._state.style = style
                return applied is not None

            if self._state is not None and self._state.captured:
                self._state.revision += 1
                cancelled = await self._request_applied(
                    Frame(
                        Operation.CANCEL,
                        self._state.session_id,
                        self._state.revision,
                    )
                )
                if not cancelled:
                    return False

            self._rollback_confirmed_tasks.discard(task_id)
            self._rollback_failed_tasks.discard(task_id)
            session_id = uuid.uuid4()
            host = HostApplication()
            state = _CompositionState(
                task_id,
                session_id,
                1,
                False,
                style,
                host,
                self._processor_registry.resolve(host),
            )
            self._state = state
            reply = await self._request_applied(
                Frame(
                    Operation.BEGIN,
                    session_id,
                    state.revision,
                    text,
                    status=style,
                )
            )
            state.captured = reply is not None
            if reply is not None:
                state.host = HostApplication(reply.process_id, reply.process_name)
                state.processor = self._processor_registry.resolve(state.host)
                _LOGGER.info(
                    "TSF host selected session=%s pid=%d process=%s processor=%s",
                    state.session_id.hex[:8],
                    state.host.process_id,
                    state.host.process_name or "unknown",
                    state.processor.name,
                )
                if (
                    state.processor.process_revision(text, style)
                    is RevisionDecision.DEFER_FINAL
                ):
                    return await self._defer_to_final(state)
            if not state.captured:
                # A foreground edit session can finish just after our timeout.
                # Queue a higher revision cancel so a late BEGIN cannot coexist
                # with the legacy keyboard/paste fallback.
                state.revision += 1
                await self._request_applied(
                    Frame(Operation.CANCEL, state.session_id, state.revision)
                )
            return state.captured

    async def _defer_to_final(self, state: _CompositionState) -> bool:
        state.defer_final = True
        _LOGGER.info(
            "TSF host processor deferred revision session=%s process=%s "
            "processor=%s decision=%s",
            state.session_id.hex[:8],
            state.host.process_name or "unknown",
            state.processor.name,
            RevisionDecision.DEFER_FINAL.value,
        )
        state.revision += 1
        cancelled = await self._request_applied(
            Frame(Operation.CANCEL, state.session_id, state.revision)
        )
        if cancelled is not None:
            self._state = None
        return False

    async def commit(self, task_id: str, final_text: str | None = None) -> bool:
        async with self._lock:
            state = self._state
            if state is None or state.task_id != task_id or not state.captured:
                return False
            if final_text is not None:
                if state.defer_final:
                    return False
                if (
                    state.processor.process_revision(final_text, state.style)
                    is RevisionDecision.DEFER_FINAL
                ):
                    state.defer_final = True
                    _LOGGER.info(
                        "TSF host processor deferred final commit session=%s "
                        "process=%s processor=%s decision=%s",
                        state.session_id.hex[:8],
                        state.host.process_name or "unknown",
                        state.processor.name,
                        RevisionDecision.DEFER_FINAL.value,
                    )
                    return False
                state.revision += 1
                revised = await self._request_applied(
                    Frame(
                        Operation.REVISE,
                        state.session_id,
                        state.revision,
                        final_text,
                        status=state.style,
                    )
                )
                if not revised:
                    return False
            state.revision += 1
            committed = await self._request_applied(
                Frame(Operation.COMMIT, state.session_id, state.revision)
            )
            if committed:
                self._state = None
            return committed is not None

    async def cancel(self, task_id: str | None = None) -> bool:
        async with self._lock:
            state = self._state
            if state is None or (task_id is not None and state.task_id != task_id):
                return False
            if state.captured:
                state.revision += 1
                cancelled = await self._request_applied(
                    Frame(Operation.CANCEL, state.session_id, state.revision)
                )
                if cancelled:
                    self._state = None
                return cancelled is not None
            self._state = None
            return False


_bridge = TsfSpeechTipBridge()


def get_tsf_speech_tip_bridge() -> TsfSpeechTipBridge:
    return _bridge
