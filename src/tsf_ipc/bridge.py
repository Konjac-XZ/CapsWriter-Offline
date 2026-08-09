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
from .context_snapshot import TsfContextSnapshot, decode_context_snapshot
from .protocol import (
    CompositionStyle,
    Frame,
    Operation,
    Status,
    TrackingSnapshotKind,
)
from .text_reconciler import (
    IncrementalTextTracker,
    has_meaningful_tracking_anchor,
    is_plausible_tracking_edit,
    reconcile_tracked_text,
)
from .windows_pipe import BrokerReply, WindowsNamedPipeBroker


_LOGGER = logging.getLogger("capswriter.tsf.bridge")
TRACKING_MAX_LIFETIME_SECONDS = 120.0


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
    text: str
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
        self._committed_sessions: dict[str, str] = {}
        self._tracked_text: dict[str, str] = {}
        self._active_tracked_session_id: str | None = None
        self._tracking_started_at = 0.0
        self._tracking_baselines: dict[str, TsfContextSnapshot] = {}
        self._tracking_incremental: dict[str, IncrementalTextTracker] = {}
        self._tracking_current: dict[str, TsfContextSnapshot] = {}
        self._tracking_flush_handle: asyncio.TimerHandle | None = None
        set_event_handler = getattr(self._broker, "set_event_handler", None)
        if set_event_handler is not None:
            set_event_handler(self._handle_event)

    def _handle_event(self, frame: Frame) -> None:
        if frame.operation == int(Operation.TRACKING_DIAGNOSTIC):
            _LOGGER.info(
                "TSF tracking diagnostic session=%s revision=%d status=%s detail=%s",
                frame.session_id.hex[:8],
                frame.revision,
                self._status_name(frame.status),
                frame.text or "-",
            )
            return
        if frame.operation == int(Operation.TRACKING_SNAPSHOT):
            self._handle_tracking_snapshot(frame)
            return
        if frame.operation == int(Operation.TRACKED_TEXT_CHANGED):
            session_id = str(frame.session_id)
            _LOGGER.info(
                "TSF live-range observation session=%s revision=%d chars=%d "
                "text_hash=%s active=%s text=%r",
                frame.session_id.hex[:8],
                frame.revision,
                len(frame.text),
                hashlib.sha256(frame.text.encode("utf-8")).hexdigest()[:8],
                session_id == self._active_tracked_session_id,
                frame.text,
            )
            return
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

    def _handle_tracking_snapshot(self, frame: Frame) -> None:
        session_id = str(frame.session_id)
        try:
            snapshot = decode_context_snapshot(frame.text)
            kind = TrackingSnapshotKind(frame.status)
        except (ValueError, UnicodeError):
            _LOGGER.exception(
                "TSF tracking snapshot rejected session=%s revision=%d",
                frame.session_id.hex[:8],
                frame.revision,
            )
            return
        _LOGGER.info(
            "TSF tracking snapshot session=%s revision=%d kind=%s "
            "target_start=%d target_end=%d text=%r",
            frame.session_id.hex[:8],
            frame.revision,
            kind.name.lower(),
            snapshot.selection_start,
            snapshot.selection_end,
            snapshot.text,
        )
        if kind is TrackingSnapshotKind.BASELINE:
            if session_id != self._active_tracked_session_id:
                _LOGGER.warning(
                    "TSF tracking baseline ignored inactive session=%s "
                    "active_session=%s",
                    frame.session_id.hex[:8],
                    (
                        self._active_tracked_session_id[:8]
                        if self._active_tracked_session_id
                        else "-"
                    ),
                )
                return
            self._tracking_baselines.clear()
            self._tracking_baselines[session_id] = snapshot
            self._tracking_incremental.clear()
            self._tracking_incremental[session_id] = IncrementalTextTracker(snapshot)
            return
        if session_id != self._active_tracked_session_id:
            _LOGGER.warning(
                "TSF tracking snapshot ignored inactive session=%s active_session=%s",
                frame.session_id.hex[:8],
                (
                    self._active_tracked_session_id[:8]
                    if self._active_tracked_session_id
                    else "-"
                ),
            )
            return
        if (
            self._tracking_started_at > 0
            and time.monotonic() - self._tracking_started_at
            >= TRACKING_MAX_LIFETIME_SECONDS
        ):
            self._stop_tracking(session_id, reason="lifetime_expired")
            return
        target_text = snapshot.text[snapshot.selection_start : snapshot.selection_end]
        if not target_text.strip():
            self._stop_tracking(session_id, reason="range_empty")
            return
        baseline = self._tracking_baselines.get(session_id)
        previous = self._tracked_text.get(session_id)
        if (
            baseline is not None
            and previous is not None
            and not has_meaningful_tracking_anchor(baseline)
            and not is_plausible_tracking_edit(previous, target_text)
        ):
            self._stop_tracking(session_id, reason="content_discontinuity")
            return
        self._tracking_current[session_id] = snapshot
        if self._tracking_flush_handle is not None:
            self._tracking_flush_handle.cancel()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._tracking_flush_handle = loop.call_later(
            0.35, self._flush_tracking_snapshot, session_id
        )

    def _flush_tracking_snapshot(self, session_id: str) -> None:
        self._tracking_flush_handle = None
        if session_id != self._active_tracked_session_id:
            return
        baseline = self._tracking_baselines.get(session_id)
        current = self._tracking_current.get(session_id)
        if baseline is None or current is None:
            return
        tracker = self._tracking_incremental.get(session_id)
        result = tracker.advance(current) if tracker is not None else None
        mode = "incremental"
        if result is None:
            result = reconcile_tracked_text(baseline, current)
            mode = "baseline_recovery"
            if result is not None:
                if tracker is None:
                    tracker = IncrementalTextTracker(baseline)
                    self._tracking_incremental[session_id] = tracker
                tracker.recover(current, result)
        if result is None:
            _LOGGER.warning(
                "TSF tracking reconciliation rejected session=%s", session_id[:8]
            )
            if not has_meaningful_tracking_anchor(baseline):
                self._stop_tracking(session_id, reason="reconciliation_rejected")
            return
        previous = self._tracked_text.get(session_id)
        if (
            previous is not None
            and not has_meaningful_tracking_anchor(baseline)
            and not is_plausible_tracking_edit(previous, result.text)
        ):
            self._stop_tracking(session_id, reason="content_discontinuity")
            return
        matched = False
        try:
            from src.polish.llm_polish import update_finalized_text

            matched = update_finalized_text(session_id, result.text)
        except Exception:  # noqa: BLE001 - edit feedback is best-effort
            _LOGGER.exception(
                "Failed to update reconciled TSF history session=%s", session_id[:8]
            )
        if matched:
            self._tracked_text[session_id] = result.text
        _LOGGER.info(
            "TSF tracking reconciliation session=%s mode=%s alignment_score=%.3f "
            "history_matched=%s text=%r",
            session_id[:8],
            mode,
            result.alignment_score,
            matched,
            result.text,
        )

    def _stop_tracking(self, session_id: str, *, reason: str) -> None:
        if session_id != self._active_tracked_session_id:
            return
        if self._tracking_flush_handle is not None:
            self._tracking_flush_handle.cancel()
            self._tracking_flush_handle = None
        self._active_tracked_session_id = None
        self._tracking_started_at = 0.0
        self._tracked_text.clear()
        self._tracking_baselines.clear()
        self._tracking_incremental.clear()
        self._tracking_current.clear()
        _LOGGER.info(
            "TSF tracking stopped session=%s reason=%s", session_id[:8], reason
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

    def get_status_snapshot(self) -> dict[str, object]:
        """Return content-free operational state for the private diagnostics GUI."""
        state = self._state
        clients = getattr(self._broker, "client_snapshots", ())
        startup_error = self.startup_error
        return {
            "enabled": self.enabled,
            "server_running": bool(getattr(self._broker, "is_running", False)),
            "client_count": int(getattr(self._broker, "client_count", 0) or 0),
            "clients": list(clients),
            "startup_error": str(startup_error) if startup_error else None,
            "composition": (
                {
                    "active": True,
                    "task_id": state.task_id,
                    "session_id": str(state.session_id),
                    "revision": state.revision,
                    "captured": state.captured,
                    "style": state.style.name.lower(),
                    "host_process_id": state.host.process_id,
                    "host_process_name": state.host.process_name or "unknown",
                    "processor": state.processor.name,
                    "defer_final": state.defer_final,
                }
                if state is not None
                else {"active": False}
            ),
        }

    def owns_task(self, task_id: str | None) -> bool:
        return bool(
            task_id
            and self._state is not None
            and self._state.task_id == task_id
            and self._state.captured
        )

    def take_committed_session_id(self, task_id: str | None) -> str | None:
        if task_id is None:
            return None
        session_id = self._committed_sessions.pop(task_id, None)
        _LOGGER.info(
            "TSF tracking history binding task=%s session=%s found=%s",
            task_id,
            session_id[:8] if session_id else "-",
            session_id is not None,
        )
        return session_id

    def get_tracked_text(self, session_id: str) -> str | None:
        return self._tracked_text.get(session_id)

    @staticmethod
    def _ack_timeout() -> float:
        return max(
            0.01,
            float(getattr(Config, "tsf_speech_tip_ack_timeout_ms", 150)) / 1000.0,
        )

    @staticmethod
    def _context_timeout() -> float:
        return max(
            0.01,
            float(getattr(Config, "tsf_speech_tip_context_timeout_ms", 500)) / 1000.0,
        )

    async def _request_applied(
        self,
        frame: Frame,
        *,
        timeout: float | None = None,
    ) -> BrokerReply | None:
        started = time.monotonic()
        clients_before = getattr(self._broker, "client_count", None)
        try:
            ack = await self._broker.request(
                frame,
                self._ack_timeout() if timeout is None else timeout,
            )
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

    async def query_context(self) -> TsfContextSnapshot | None:
        if not self.enabled:
            return None
        timeout = self._context_timeout()
        started = time.monotonic()
        if getattr(self._broker, "client_count", None) == 0:
            wait_for_client = getattr(self._broker, "wait_for_client", None)
            if wait_for_client is not None:
                try:
                    if not await wait_for_client(timeout):
                        return None
                except asyncio.CancelledError:
                    raise
                except Exception:  # noqa: BLE001 - UIA can still provide context
                    return None
        remaining = max(0.01, timeout - (time.monotonic() - started))
        async with self._lock:
            reply = await self._request_applied(
                Frame(Operation.QUERY_CONTEXT, uuid.uuid4(), 1),
                timeout=remaining,
            )
        if reply is None or not reply.frame.text:
            return None
        try:
            return decode_context_snapshot(
                reply.frame.text,
                process_id=reply.process_id,
                process_name=reply.process_name,
            )
        except ValueError as exc:
            _LOGGER.warning(
                "TSF context response rejected process=%s pid=%d error=%s",
                reply.process_name or "unknown",
                reply.process_id,
                type(exc).__name__,
            )
            return None

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
        log = _LOGGER.info if result == Status.APPLIED.name.lower() else _LOGGER.warning
        log(
            "TSF request op=%s session=%s revision=%d style=%d chars=%d "
            "clients=%s result=%s elapsed_ms=%.1f%s",
            self._operation_name(int(frame.operation)),
            frame.session_id.hex[:8],
            frame.revision,
            int(frame.status),
            len(frame.text),
            clients if clients is not None else "unknown",
            result,
            (time.monotonic() - started) * 1000.0,
            f" error={error}" if error else "",
        )
        if frame.text and _LOGGER.isEnabledFor(logging.DEBUG):
            _LOGGER.debug(
                "TSF request payload op=%s session=%s revision=%d text_hash=%s text=%r",
                self._operation_name(int(frame.operation)),
                frame.session_id.hex[:8],
                frame.revision,
                hashlib.sha256(frame.text.encode("utf-8")).hexdigest()[:8],
                frame.text,
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
                    self._state.text = text
                return applied is not None

            # Once a new speech task starts, no later edit event may revise the
            # preceding history item. This also protects against hosts whose
            # committed TSF range has forward gravity.
            if self._active_tracked_session_id is not None:
                self._stop_tracking(
                    self._active_tracked_session_id, reason="new_speech_task"
                )

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
                text,
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
                state.text = final_text
            state.revision += 1
            session_id = str(state.session_id)
            self._active_tracked_session_id = session_id
            self._tracking_started_at = time.monotonic()
            committed = await self._request_applied(
                Frame(Operation.COMMIT, state.session_id, state.revision)
            )
            if committed:
                self._committed_sessions[task_id] = session_id
                self._tracked_text.clear()
                if self._active_tracked_session_id == session_id:
                    self._tracked_text[session_id] = state.text
                self._state = None
            else:
                self._stop_tracking(session_id, reason="commit_failed")
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
