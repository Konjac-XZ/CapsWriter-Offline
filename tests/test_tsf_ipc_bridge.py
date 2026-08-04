import asyncio

import pytest

from src.tsf_ipc.bridge import TsfSpeechTipBridge
from src.tsf_ipc.protocol import CompositionStyle, Frame, Operation, Status
from src.tsf_ipc.windows_pipe import BrokerReply
from src.tsf_ipc.context_snapshot import encode_context_snapshot


class FakeBroker:
    startup_error: Exception | None = None

    def __init__(
        self,
        default_status=Status.APPLIED,
        responses=None,
        *,
        process_id=1234,
        process_name="Editor.exe",
        response_text="",
    ):
        self.default_status = default_status
        self.responses = {
            int(operation): list(values)
            for operation, values in (responses or {}).items()
        }
        self.frames: list[Frame] = []
        self.event_handler = None
        self.process_id = process_id
        self.process_name = process_name
        self.response_text = response_text

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
        return BrokerReply(
            Frame(
                int(Operation.ACK_FLAG) | int(frame.operation),
                frame.session_id,
                frame.revision,
                text=self.response_text,
                status=response,
            ),
            process_id=self.process_id,
            process_name=self.process_name,
        )

    def broadcast(self, frame):
        self.frames.append(frame)
        return 1

    def set_event_handler(self, handler):
        self.event_handler = handler

    def emit_event(self, frame):
        assert self.event_handler is not None
        self.event_handler(frame)


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
        assert (
            await bridge.begin_or_revise(
                "task-1", "完整的第二版", CompositionStyle.POLISHING
            )
            is True
        )
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
    assert [frame.status for frame in broker.frames] == [
        CompositionStyle.TRANSCRIPTION,
        CompositionStyle.POLISHING,
        CompositionStyle.POLISHING,
        0,
    ]


def test_bridge_queries_context_from_actual_applied_host(enable_bridge):
    broker = FakeBroker(
        process_id=4321,
        process_name="ChatGPT.exe",
        response_text=encode_context_snapshot("前文😀", "选中", "后文"),
    )
    bridge = TsfSpeechTipBridge(broker)

    snapshot = asyncio.run(bridge.query_context())

    assert snapshot is not None
    assert snapshot.text == "前文😀选中后文"
    assert snapshot.caret_offset == len("前文😀选中")
    assert snapshot.selection_start == len("前文😀")
    assert snapshot.selection_end == len("前文😀选中")
    assert snapshot.process_id == 4321
    assert snapshot.process_name == "ChatGPT.exe"
    assert [frame.operation for frame in broker.frames] == [Operation.QUERY_CONTEXT]


def test_bridge_rejects_invalid_context_payload(enable_bridge):
    bridge = TsfSpeechTipBridge(FakeBroker(response_text="invalid"))

    assert asyncio.run(bridge.query_context()) is None


def test_context_query_waits_for_first_foreground_tip_connection(enable_bridge):
    class DelayedBroker(FakeBroker):
        client_count = 0

        async def wait_for_client(self, _timeout):
            self.client_count = 1
            return True

    broker = DelayedBroker(response_text=encode_context_snapshot("前文", "", "后文"))
    bridge = TsfSpeechTipBridge(broker)

    snapshot = asyncio.run(bridge.query_context())

    assert snapshot is not None
    assert snapshot.text == "前文后文"


def test_chatgpt_single_line_polishing_keeps_streaming(enable_bridge):
    broker = FakeBroker(process_name="ChatGPT.exe")
    bridge = TsfSpeechTipBridge(broker)

    async def exercise():
        assert await bridge.begin_or_revise("task-1", "原文") is True
        assert (
            await bridge.begin_or_revise(
                "task-1", "单行润色", CompositionStyle.POLISHING
            )
            is True
        )

    asyncio.run(exercise())

    assert [frame.operation for frame in broker.frames] == [
        Operation.BEGIN,
        Operation.REVISE,
    ]


def test_chatgpt_multiline_revision_cancels_before_sending_it(enable_bridge):
    broker = FakeBroker(process_name="ChatGPT.exe")
    bridge = TsfSpeechTipBridge(broker)

    async def exercise():
        assert await bridge.begin_or_revise("task-1", "原文") is True
        assert (
            await bridge.begin_or_revise(
                "task-1", "第一段\n第二段", CompositionStyle.POLISHING
            )
            is False
        )
        assert bridge.owns_task("task-1") is False

    asyncio.run(exercise())

    assert [frame.operation for frame in broker.frames] == [
        Operation.BEGIN,
        Operation.CANCEL,
    ]


def test_chatgpt_failed_preemptive_cancel_blocks_revisions_and_commit(enable_bridge):
    broker = FakeBroker(
        responses={
            Operation.CANCEL: [Status.EDIT_SESSION_FAILED, Status.APPLIED],
        },
        process_name="ChatGPT.exe",
    )
    bridge = TsfSpeechTipBridge(broker)

    async def exercise():
        assert await bridge.begin_or_revise("task-1", "原文") is True
        assert await bridge.begin_or_revise("task-1", "第一段\n第二段") is False
        assert bridge.owns_task("task-1") is True
        assert await bridge.begin_or_revise("task-1", "后续单行 token") is False
        assert await bridge.commit("task-1", "最终\n文本") is False
        assert await bridge.cancel("task-1") is True

    asyncio.run(exercise())

    assert [frame.operation for frame in broker.frames] == [
        Operation.BEGIN,
        Operation.CANCEL,
        Operation.CANCEL,
    ]


def test_chatgpt_multiline_final_commit_defers_to_confirmed_cancel(enable_bridge):
    broker = FakeBroker(process_name="ChatGPT.exe")
    bridge = TsfSpeechTipBridge(broker)

    async def exercise():
        assert await bridge.begin_or_revise("task-1", "原文") is True
        assert await bridge.commit("task-1", "最终\n文本") is False
        assert bridge.owns_task("task-1") is True
        assert await bridge.cancel("task-1") is True

    asyncio.run(exercise())

    assert [frame.operation for frame in broker.frames] == [
        Operation.BEGIN,
        Operation.CANCEL,
    ]


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


def test_external_termination_releases_ownership_after_confirmed_rollback(
    enable_bridge,
):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)

    assert asyncio.run(bridge.begin_or_revise("task-1", "未完成原文")) is True
    begin = broker.frames[0]
    broker.emit_event(
        Frame(
            Operation.COMPOSITION_TERMINATED,
            begin.session_id,
            begin.revision,
            status=Status.APPLIED,
        )
    )

    assert bridge.owns_task("task-1") is False
    assert bridge.take_confirmed_termination_rollback("task-1") is True
    assert bridge.take_confirmed_termination_rollback("task-1") is False


def test_external_termination_does_not_allow_fallback_when_rollback_failed(
    enable_bridge,
):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)

    assert asyncio.run(bridge.begin_or_revise("task-1", "未完成原文")) is True
    begin = broker.frames[0]
    broker.emit_event(
        Frame(
            Operation.COMPOSITION_TERMINATED,
            begin.session_id,
            begin.revision,
            status=Status.EDIT_SESSION_FAILED,
        )
    )

    assert bridge.owns_task("task-1") is False
    assert bridge.take_confirmed_termination_rollback("task-1") is False
    assert bridge.take_failed_termination_rollback("task-1") is True
    assert bridge.take_failed_termination_rollback("task-1") is False


def test_chatgpt_does_not_trust_applied_external_termination_rollback(
    enable_bridge,
):
    broker = FakeBroker(process_name="ChatGPT.exe")
    bridge = TsfSpeechTipBridge(broker)

    assert asyncio.run(bridge.begin_or_revise("task-1", "未完成原文")) is True
    begin = broker.frames[0]
    broker.emit_event(
        Frame(
            Operation.COMPOSITION_TERMINATED,
            begin.session_id,
            begin.revision,
            status=Status.APPLIED,
        )
    )

    assert bridge.owns_task("task-1") is False
    assert bridge.take_confirmed_termination_rollback("task-1") is False
    assert bridge.take_failed_termination_rollback("task-1") is True
