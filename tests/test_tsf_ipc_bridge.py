import asyncio

import pytest

from src.tsf_ipc.bridge import TsfSpeechTipBridge
from src.tsf_ipc.protocol import Frame, Operation, Status


class FakeBroker:
    startup_error: Exception | None = None

    def __init__(self, begin_status=Status.APPLIED):
        self.begin_status = begin_status
        self.frames: list[Frame] = []

    def start(self, loop=None):
        return True

    def stop(self):
        return None

    async def request(self, frame, timeout):
        self.frames.append(frame)
        if self.begin_status is None:
            return None
        return Frame(
            int(Operation.ACK_FLAG) | int(frame.operation),
            frame.session_id,
            frame.revision,
            status=self.begin_status,
        )

    def broadcast(self, frame):
        self.frames.append(frame)
        return 1


@pytest.fixture
def enable_bridge(monkeypatch):
    monkeypatch.setattr("src.tsf_ipc.bridge.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "src.tsf_ipc.bridge.Config.tsf_speech_tip_enabled", True, raising=False
    )
    monkeypatch.setattr(
        "src.tsf_ipc.bridge.Config.tsf_speech_tip_ack_timeout_ms", 25, raising=False
    )


def test_bridge_sends_full_text_revisions_then_commits(enable_bridge):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)

    async def exercise():
        assert await bridge.begin_or_revise("task-1", "第一版") is True
        assert await bridge.begin_or_revise("task-1", "完整的第二版") is True
        assert await bridge.commit("task-1", "最终文本") is True

    asyncio.run(exercise())

    assert [frame.operation for frame in broker.frames] == [
        Operation.BEGIN,
        Operation.REVISE,
        Operation.REVISE,
        Operation.COMMIT,
    ]
    assert [frame.revision for frame in broker.frames] == [1, 2, 3, 4]
    assert [frame.text for frame in broker.frames] == [
        "第一版",
        "完整的第二版",
        "最终文本",
        "",
    ]
    assert len({frame.session_id for frame in broker.frames}) == 1


def test_bridge_keeps_legacy_path_when_foreground_tip_does_not_ack(enable_bridge):
    broker = FakeBroker(Status.IGNORED_NOT_FOREGROUND)
    bridge = TsfSpeechTipBridge(broker)

    assert asyncio.run(bridge.begin_or_revise("task-1", "第一版")) is False
    assert bridge.owns_task("task-1") is False
    assert asyncio.run(bridge.begin_or_revise("task-1", "第二版")) is False
    assert [frame.operation for frame in broker.frames] == [
        Operation.BEGIN,
        Operation.CANCEL,
    ]


def test_bridge_cancels_previous_composition_before_new_task(enable_bridge):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)

    async def exercise():
        assert await bridge.begin_or_revise("task-1", "旧任务") is True
        assert await bridge.begin_or_revise("task-2", "新任务") is True

    asyncio.run(exercise())

    assert [frame.operation for frame in broker.frames] == [
        Operation.BEGIN,
        Operation.CANCEL,
        Operation.BEGIN,
    ]
    assert broker.frames[0].session_id != broker.frames[2].session_id
