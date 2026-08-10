from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from typing import Callable

import pytest

from src.tsf_ipc.bridge import TsfSpeechTipBridge
from src.tsf_ipc.context_snapshot import encode_context_snapshot
from src.tsf_ipc.protocol import Frame, Operation, Status, TrackingSnapshotKind
from src.tsf_ipc.windows_pipe import BrokerReply


class _FakeBroker:
    startup_error: Exception | None = None

    def __init__(self, *, process_name: str) -> None:
        self.process_name = process_name
        self.event_handler = None

    def start(self, loop=None):
        return True

    def stop(self):
        return None

    async def request(self, frame, timeout):
        return BrokerReply(
            Frame(
                int(Operation.ACK_FLAG) | int(frame.operation),
                frame.session_id,
                frame.revision,
                status=Status.APPLIED,
            ),
            process_id=1234,
            process_name=self.process_name,
        )

    def broadcast(self, frame):
        return 1

    def set_event_handler(self, handler):
        self.event_handler = handler

    def emit_event(self, frame: Frame) -> None:
        assert self.event_handler is not None
        self.event_handler(frame)


@dataclass(slots=True)
class BridgeCorrectionCase:
    bridge: TsfSpeechTipBridge
    broker: _FakeBroker
    updates: list[tuple[str, str]]

    def commit(self, text: str) -> str:
        async def exercise() -> str:
            assert await self.bridge.begin_or_revise("test-case", text) is True
            assert await self.bridge.commit("test-case", text) is True
            session_id = self.bridge.take_committed_session_id("test-case")
            assert session_id is not None
            return session_id

        return asyncio.run(exercise())

    def observe(
        self,
        session_id: str,
        revision: int,
        kind: TrackingSnapshotKind,
        *,
        before: str,
        tracked: str,
        after: str,
    ) -> None:
        self.broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                revision,
                encode_context_snapshot(before, tracked, after),
                kind,
            )
        )

    def settle(self, session_id: str) -> None:
        """Deterministically process the latest stable observation."""
        self.bridge._flush_tracking_snapshot(session_id)


@pytest.fixture
def make_bridge_correction_case(
    monkeypatch,
) -> Callable[[str], BridgeCorrectionCase]:
    monkeypatch.setattr("src.tsf_ipc.bridge.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "src.tsf_ipc.bridge.Config.tsf_speech_tip_enabled", True, raising=False
    )
    monkeypatch.setattr(
        "src.tsf_ipc.bridge.Config.tsf_speech_tip_ack_timeout_ms", 25, raising=False
    )

    def make(process_name: str) -> BridgeCorrectionCase:
        updates: list[tuple[str, str]] = []
        monkeypatch.setattr(
            "src.polish.llm_polish.update_finalized_text",
            lambda session_id, text: updates.append((session_id, text)) or True,
        )
        broker = _FakeBroker(process_name=process_name)
        return BridgeCorrectionCase(TsfSpeechTipBridge(broker), broker, updates)

    return make
