import asyncio

import pytest

from src.tsf_ipc.bridge import TsfSpeechTipBridge
from src.tsf_ipc.protocol import Frame, Operation, Status


class FakeBroker:
    startup_error: Exception | None = None

    def __init__(self, default_status=Status.APPLIED, responses=None):
        self.default_status = default_status
        self.responses = {
            int(operation): list(values)
            for operation, values in (responses or {}).items()
        }
        self.frames: list[Frame] = []

    def start(self, loop=None):
        return True

    def stop(self):
        return None

    async def request(self, frame, timeout):
        self.frames.append(frame)
        responses = self.responses.get(int(frame.operation))
        response = responses.pop(0) if responses else self.default_status
        if isinstance(response, Exception):
            raise response
        if response is None:
            return None
        return Frame(
            int(Operation.ACK_FLAG) | int(frame.operation),
            frame.session_id,
            frame.revision,
            status=response,
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


def test_bridge_waits_for_every_revision_and_commit_in_order(enable_bridge):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)

    async def exercise():
        assert await bridge.begin_or_revise("task-1", "第一版") is True
        assert await bridge.begin_or_revise("task-1", "第二版") is True
        assert await bridge.commit("task-1", "最终版") is True
        assert bridge.owns_task("task-1") is False

    asyncio.run(exercise())

    assert [frame.operation for frame in broker.frames] == [
        Operation.BEGIN,
        Operation.REVISE,
        Operation.REVISE,
        Operation.COMMIT,
    ]
    assert [frame.revision for frame in broker.frames] == [1, 2, 3, 4]


@pytest.mark.parametrize(
    "response",
    [Status.EDIT_SESSION_FAILED, None, ConnectionError("TIP disconnected")],
)
def test_failed_final_revision_does_not_send_commit_or_release_state(
    enable_bridge, response
):
    broker = FakeBroker(responses={Operation.REVISE: [response]})
    bridge = TsfSpeechTipBridge(broker)

    async def exercise():
        assert await bridge.begin_or_revise("task-1", "草稿") is True
        assert await bridge.commit("task-1", "最终版") is False
        assert bridge.owns_task("task-1") is True

    asyncio.run(exercise())

    assert [frame.operation for frame in broker.frames] == [
        Operation.BEGIN,
        Operation.REVISE,
    ]


def test_failed_streaming_revision_keeps_composition_ownership(enable_bridge):
    broker = FakeBroker(responses={Operation.REVISE: [Status.EDIT_SESSION_FAILED]})
    bridge = TsfSpeechTipBridge(broker)

    async def exercise():
        assert await bridge.begin_or_revise("task-1", "第一版") is True
        assert await bridge.begin_or_revise("task-1", "第二版") is False
        assert bridge.owns_task("task-1") is True

    asyncio.run(exercise())


@pytest.mark.parametrize(
    "response",
    [Status.INACTIVE_SESSION, None, ConnectionError("TIP disconnected")],
)
def test_failed_commit_keeps_state_available_for_recovery(enable_bridge, response):
    broker = FakeBroker(responses={Operation.COMMIT: [response]})
    bridge = TsfSpeechTipBridge(broker)

    async def exercise():
        assert await bridge.begin_or_revise("task-1", "草稿") is True
        assert await bridge.commit("task-1", "最终版") is False
        assert bridge.owns_task("task-1") is True

    asyncio.run(exercise())

    assert [frame.operation for frame in broker.frames] == [
        Operation.BEGIN,
        Operation.REVISE,
        Operation.COMMIT,
    ]


def test_cancel_only_releases_state_after_applied_ack(enable_bridge):
    broker = FakeBroker(
        responses={
            Operation.CANCEL: [Status.EDIT_SESSION_FAILED, Status.APPLIED],
        }
    )
    bridge = TsfSpeechTipBridge(broker)

    async def exercise():
        assert await bridge.begin_or_revise("task-1", "草稿") is True
        assert await bridge.cancel("task-1") is False
        assert bridge.owns_task("task-1") is True
        assert await bridge.cancel("task-1") is True
        assert bridge.owns_task("task-1") is False

    asyncio.run(exercise())

    assert [frame.revision for frame in broker.frames] == [1, 2, 3]
