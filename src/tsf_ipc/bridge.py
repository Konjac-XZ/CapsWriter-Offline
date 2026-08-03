from __future__ import annotations

import asyncio
import platform
import uuid
from dataclasses import dataclass
from typing import Protocol

from src.infra.config import config as Config

from .protocol import CompositionStyle, Frame, Operation, Status
from .windows_pipe import WindowsNamedPipeBroker


class SpeechTipBroker(Protocol):
    @property
    def startup_error(self) -> Exception | None: ...

    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> bool: ...

    def stop(self) -> None: ...

    async def request(self, frame: Frame, timeout: float) -> Frame | None: ...

    def broadcast(self, frame: Frame) -> int: ...


@dataclass(slots=True)
class _CompositionState:
    task_id: str
    session_id: uuid.UUID
    revision: int
    captured: bool
    style: CompositionStyle


class TsfSpeechTipBridge:
    def __init__(self, broker: SpeechTipBroker | None = None) -> None:
        self._broker = broker or WindowsNamedPipeBroker()
        self._state: _CompositionState | None = None
        self._lock = asyncio.Lock()

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

    async def _request_applied(self, frame: Frame) -> bool:
        try:
            ack = await self._broker.request(frame, self._ack_timeout())
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - transport failures all mean no ACK
            return False
        return bool(
            ack
            and ack.is_ack
            and ack.acknowledged_operation == int(frame.operation)
            and ack.session_id == frame.session_id
            and ack.revision == frame.revision
            and ack.status == int(Status.APPLIED)
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
                return applied

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

            session_id = uuid.uuid4()
            state = _CompositionState(task_id, session_id, 1, False, style)
            self._state = state
            state.captured = await self._request_applied(
                Frame(
                    Operation.BEGIN,
                    session_id,
                    state.revision,
                    text,
                    status=style,
                )
            )
            if not state.captured:
                # A foreground edit session can finish just after our timeout.
                # Queue a higher revision cancel so a late BEGIN cannot coexist
                # with the legacy keyboard/paste fallback.
                state.revision += 1
                await self._request_applied(
                    Frame(Operation.CANCEL, state.session_id, state.revision)
                )
            return state.captured

    async def commit(self, task_id: str, final_text: str | None = None) -> bool:
        async with self._lock:
            state = self._state
            if state is None or state.task_id != task_id or not state.captured:
                return False
            if final_text is not None:
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
            return committed

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
                return cancelled
            self._state = None
            return False


_bridge = TsfSpeechTipBridge()


def get_tsf_speech_tip_bridge() -> TsfSpeechTipBridge:
    return _bridge
