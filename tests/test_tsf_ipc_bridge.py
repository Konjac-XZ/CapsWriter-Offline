import asyncio
import uuid

import pytest

from src.tsf_ipc.bridge import TsfSpeechTipBridge
from src.tsf_ipc.protocol import (
    CompositionStyle,
    Frame,
    Operation,
    Status,
    TrackingSnapshotKind,
)
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


def test_applied_request_info_log_omits_full_text_and_hash(enable_bridge, caplog):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)
    secret_text = "不应出现在默认日志中的正文"

    caplog.set_level("INFO", logger="capswriter.tsf.bridge")
    assert asyncio.run(bridge.begin_or_revise("task-log", secret_text)) is True

    assert secret_text not in caplog.text
    assert "text_hash=" not in caplog.text
    assert f"chars={len(secret_text)}" in caplog.text


def test_live_range_event_is_diagnostic_only(enable_bridge, monkeypatch):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)
    updates = []
    monkeypatch.setattr(
        "src.polish.llm_polish.update_finalized_text",
        lambda session_id, text: updates.append((session_id, text)) or True,
    )

    asyncio.run(bridge.begin_or_revise("task-1", "原文"))
    asyncio.run(bridge.commit("task-1", "最终原文"))
    session_id = bridge.take_committed_session_id("task-1")
    assert session_id is not None

    broker.emit_event(
        Frame(Operation.TRACKED_TEXT_CHANGED, uuid.UUID(session_id), 4, "用户改文")
    )

    assert bridge.get_tracked_text(session_id) == "最终原文"
    assert updates == []


def test_stable_snapshot_reconciles_and_updates_history(enable_bridge, monkeypatch):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)
    updates = []
    monkeypatch.setattr(
        "src.polish.llm_polish.update_finalized_text",
        lambda session_id, text: updates.append((session_id, text)) or True,
    )

    async def exercise():
        await bridge.begin_or_revise("task-1", "你好，测试。")
        await bridge.commit("task-1", "你好，测试。")
        session_id = bridge.take_committed_session_id("task-1")
        assert session_id is not None
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                3,
                encode_context_snapshot("前文\n", "你好，测试。", "\n后文"),
                TrackingSnapshotKind.BASELINE,
            )
        )
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                4,
                encode_context_snapshot("前文\n您", "好，测试。", "\n后文"),
                TrackingSnapshotKind.CURRENT,
            )
        )
        await asyncio.sleep(0.4)
        return session_id

    session_id = asyncio.run(exercise())

    assert updates == [(session_id, "您好，测试。")]
    assert bridge.get_tracked_text(session_id) == "您好，测试。"


def test_consecutive_stable_snapshots_advance_incremental_range(
    enable_bridge, monkeypatch
):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)
    updates = []
    monkeypatch.setattr(
        "src.polish.llm_polish.update_finalized_text",
        lambda session_id, text: updates.append((session_id, text)) or True,
    )

    async def exercise():
        await bridge.begin_or_revise("task-1", "第一版。")
        await bridge.commit("task-1", "第一版。")
        session_id = bridge.take_committed_session_id("task-1")
        assert session_id is not None
        snapshots = [
            ("前文\n", "第一版。", "\n后文", TrackingSnapshotKind.BASELINE),
            ("前文\n", "第二版完全不同。", "\n后文", TrackingSnapshotKind.CURRENT),
        ]
        for prefix, selection, suffix, kind in snapshots:
            broker.emit_event(
                Frame(
                    Operation.TRACKING_SNAPSHOT,
                    uuid.UUID(session_id),
                    3,
                    encode_context_snapshot(prefix, selection, suffix),
                    kind,
                )
            )
        await asyncio.sleep(0.4)
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                4,
                encode_context_snapshot("前文也变了\n", "最终内容。", "\n后文"),
                TrackingSnapshotKind.CURRENT,
            )
        )
        await asyncio.sleep(0.4)
        return session_id

    session_id = asyncio.run(exercise())

    assert updates == [
        (session_id, "第二版完全不同。"),
        (session_id, "最终内容。"),
    ]


def test_tracking_diagnostic_is_logged(enable_bridge, caplog):
    broker = FakeBroker()
    TsfSpeechTipBridge(broker)
    session_id = uuid.uuid4()

    with caplog.at_level("INFO", logger="capswriter.tsf.bridge"):
        broker.emit_event(
            Frame(
                Operation.TRACKING_DIAGNOSTIC,
                session_id,
                3,
                "end_edit unchanged chars=4",
                Status.APPLIED,
            )
        )

    assert "end_edit unchanged chars=4" in caplog.text
    assert session_id.hex[:8] in caplog.text


def test_new_task_rejects_late_event_from_previous_session(enable_bridge, monkeypatch):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)
    updates = []
    monkeypatch.setattr(
        "src.polish.llm_polish.update_finalized_text",
        lambda session_id, text: updates.append((session_id, text)) or True,
    )

    asyncio.run(bridge.begin_or_revise("task-1", "第一条"))
    asyncio.run(bridge.commit("task-1", "第一条"))
    old_session = bridge.take_committed_session_id("task-1")
    assert old_session is not None
    asyncio.run(bridge.begin_or_revise("task-2", "第二条"))

    broker.emit_event(
        Frame(Operation.TRACKED_TEXT_CHANGED, uuid.UUID(old_session), 10, "第二条")
    )

    assert updates == []
    assert bridge.get_tracked_text(old_session) is None


def test_empty_snapshot_stops_tracking_without_marking_history_deleted(
    enable_bridge, monkeypatch
):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)
    updates = []
    monkeypatch.setattr(
        "src.polish.llm_polish.update_finalized_text",
        lambda session_id, text: updates.append((session_id, text)) or True,
    )

    async def exercise():
        await bridge.begin_or_revise("task-1", "第一条")
        await bridge.commit("task-1", "第一条")
        session_id = bridge.take_committed_session_id("task-1")
        assert session_id is not None
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                3,
                encode_context_snapshot("", "第一条", "\n"),
                TrackingSnapshotKind.BASELINE,
            )
        )
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                4,
                encode_context_snapshot("", "", "\n"),
                TrackingSnapshotKind.CURRENT,
            )
        )
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                5,
                encode_context_snapshot("", "下一条", "\n"),
                TrackingSnapshotKind.CURRENT,
            )
        )
        await asyncio.sleep(0.4)
        return session_id

    session_id = asyncio.run(exercise())

    assert updates == []
    assert bridge.get_tracked_text(session_id) is None


def test_empty_snapshot_during_commit_does_not_reactivate_tracking(enable_bridge):
    class EmptyDuringCommitBroker(FakeBroker):
        async def request(self, frame, timeout):
            reply = await super().request(frame, timeout)
            if frame.operation == int(Operation.COMMIT):
                self.emit_event(
                    Frame(
                        Operation.TRACKING_SNAPSHOT,
                        frame.session_id,
                        frame.revision,
                        encode_context_snapshot("", "第一条", "\n"),
                        TrackingSnapshotKind.BASELINE,
                    )
                )
                self.emit_event(
                    Frame(
                        Operation.TRACKING_SNAPSHOT,
                        frame.session_id,
                        frame.revision + 1,
                        encode_context_snapshot("", "", "\n"),
                        TrackingSnapshotKind.CURRENT,
                    )
                )
            return reply

    bridge = TsfSpeechTipBridge(EmptyDuringCommitBroker())

    async def exercise():
        await bridge.begin_or_revise("task-1", "第一条")
        assert await bridge.commit("task-1", "第一条") is True
        return bridge.take_committed_session_id("task-1")

    session_id = asyncio.run(exercise())

    assert session_id is not None
    assert bridge.get_tracked_text(session_id) is None


def test_unanchored_unrelated_replacement_stops_previous_session(
    enable_bridge, monkeypatch
):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)
    updates = []
    monkeypatch.setattr(
        "src.polish.llm_polish.update_finalized_text",
        lambda session_id, text: updates.append((session_id, text)) or True,
    )

    async def exercise():
        original = "我刚刚启用了它，但是没看到日志。"
        await bridge.begin_or_revise("task-1", original)
        await bridge.commit("task-1", original)
        session_id = bridge.take_committed_session_id("task-1")
        assert session_id is not None
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                3,
                encode_context_snapshot("", original, "\n\n"),
                TrackingSnapshotKind.BASELINE,
            )
        )
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                4,
                encode_context_snapshot("", "这是下一条完全无关的输入。", "\n\n"),
                TrackingSnapshotKind.CURRENT,
            )
        )
        await asyncio.sleep(0.4)
        return session_id

    session_id = asyncio.run(exercise())

    assert updates == []
    assert bridge.get_tracked_text(session_id) is None


def test_unanchored_local_edit_still_updates_history(enable_bridge, monkeypatch):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)
    updates = []
    monkeypatch.setattr(
        "src.polish.llm_polish.update_finalized_text",
        lambda session_id, text: updates.append((session_id, text)) or True,
    )

    async def exercise():
        original = "没看到上游请求当中有负载相关的信息。"
        corrected = "没看到上游请求当中有附带相关的信息。"
        await bridge.begin_or_revise("task-1", original)
        await bridge.commit("task-1", original)
        session_id = bridge.take_committed_session_id("task-1")
        assert session_id is not None
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                3,
                encode_context_snapshot("", original, "\n\n"),
                TrackingSnapshotKind.BASELINE,
            )
        )
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                4,
                encode_context_snapshot("", corrected, "\n\n"),
                TrackingSnapshotKind.CURRENT,
            )
        )
        await asyncio.sleep(0.4)
        return session_id, corrected

    session_id, corrected = asyncio.run(exercise())

    assert updates == [(session_id, corrected)]
    assert bridge.get_tracked_text(session_id) == corrected


def test_tracking_lifetime_expiry_rejects_late_snapshot(enable_bridge, monkeypatch):
    broker = FakeBroker()
    bridge = TsfSpeechTipBridge(broker)
    updates = []
    now = [10.0]
    monkeypatch.setattr("src.tsf_ipc.bridge.time.monotonic", lambda: now[0])
    monkeypatch.setattr(
        "src.polish.llm_polish.update_finalized_text",
        lambda session_id, text: updates.append((session_id, text)) or True,
    )

    async def exercise():
        await bridge.begin_or_revise("task-1", "原始内容")
        await bridge.commit("task-1", "原始内容")
        session_id = bridge.take_committed_session_id("task-1")
        assert session_id is not None
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                3,
                encode_context_snapshot("", "原始内容", "\n"),
                TrackingSnapshotKind.BASELINE,
            )
        )
        now[0] = 131.0
        broker.emit_event(
            Frame(
                Operation.TRACKING_SNAPSHOT,
                uuid.UUID(session_id),
                4,
                encode_context_snapshot("", "原始内容已修改", "\n"),
                TrackingSnapshotKind.CURRENT,
            )
        )
        return session_id

    session_id = asyncio.run(exercise())

    assert updates == []
    assert bridge.get_tracked_text(session_id) is None


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
