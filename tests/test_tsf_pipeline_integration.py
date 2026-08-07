import asyncio
from typing import Any, cast

import pytest

from src.infra.cosmic import Cosmic
from src.pipeline import recv_result as pipeline
from src.tsf_ipc.bridge import TsfSpeechTipBridge
from src.tsf_ipc.protocol import CompositionStyle
from src.tsf_ipc.protocol import Frame, Operation, Status
from src.tsf_ipc.windows_pipe import BrokerReply


class OneMessageQueue:
    def __init__(self, message):
        self.message = message
        self.returned = False

    async def get(self):
        if not self.returned:
            self.returned = True
            return self.message
        raise RuntimeError("end test receive loop")

    def task_done(self):
        return None


class MessageThenWaitQueue:
    def __init__(self, message):
        self.message = message
        self.returned = False
        self.waiting = asyncio.Event()

    async def get(self):
        if not self.returned:
            self.returned = True
            return self.message
        self.waiting.set()
        await asyncio.Future()

    def task_done(self):
        return None


class FakeTsfBridge:
    def __init__(self, *, commit_result=True, cancel_result=True):
        self.owned_task = None
        self.revisions = []
        self.commits = []
        self.cancels = []
        self.commit_result = commit_result
        self.cancel_result = cancel_result
        self.termination_rollback_tasks = set()
        self.failed_termination_rollback_tasks = set()

    async def begin_or_revise(
        self, task_id, text, style=CompositionStyle.TRANSCRIPTION
    ):
        self.owned_task = task_id
        self.revisions.append((task_id, text, style))
        return True

    def owns_task(self, task_id):
        return task_id == self.owned_task

    async def commit(self, task_id, text):
        self.commits.append((task_id, text))
        if self.commit_result:
            self.owned_task = None
        return self.commit_result

    async def cancel(self, task_id=None):
        self.cancels.append(task_id)
        if self.cancel_result:
            self.owned_task = None
        return self.cancel_result

    def take_confirmed_termination_rollback(self, task_id):
        if task_id not in self.termination_rollback_tasks:
            return False
        self.termination_rollback_tasks.remove(task_id)
        return True

    def take_failed_termination_rollback(self, task_id):
        if task_id not in self.failed_termination_rollback_tasks:
            return False
        self.failed_termination_rollback_tasks.remove(task_id)
        return True


class RejectingTsfBridge(FakeTsfBridge):
    async def begin_or_revise(
        self, task_id, text, style=CompositionStyle.TRANSCRIPTION
    ):
        self.revisions.append((task_id, text, style))
        return False


class OwnedButUnconfirmedTsfBridge(FakeTsfBridge):
    async def begin_or_revise(
        self, task_id, text, style=CompositionStyle.TRANSCRIPTION
    ):
        self.owned_task = task_id
        self.revisions.append((task_id, text, style))
        return False


class ChatGptAckBroker:
    startup_error = None

    def __init__(self, cancel_status=Status.APPLIED):
        self.frames = []
        self.cancel_status = cancel_status
        self.event_handler = None

    def start(self, loop=None):
        return True

    def stop(self):
        return None

    async def request(self, frame, timeout):
        self.frames.append(frame)
        status = (
            self.cancel_status
            if frame.operation == Operation.CANCEL
            else Status.APPLIED
        )
        return BrokerReply(
            Frame(
                int(Operation.ACK_FLAG) | int(frame.operation),
                frame.session_id,
                frame.revision,
                status=status,
            ),
            process_id=1234,
            process_name="ChatGPT.exe",
        )

    def broadcast(self, frame):
        self.frames.append(frame)
        return 1

    def set_event_handler(self, handler):
        self.event_handler = handler


@pytest.mark.parametrize(
    (
        "commit_result",
        "cancel_result",
        "expected_typed",
        "expected_recorded",
        "expected_cancel",
        "expected_sound_count",
    ),
    [
        (True, True, [], ["LLM final full text"], [], 1),
        (
            False,
            True,
            ["LLM final full text"],
            ["LLM final full text"],
            ["task-1"],
            1,
        ),
        (False, False, [], [], ["task-1", "task-1"], 0),
    ],
)
def test_final_tsf_output_requires_confirmed_commit_or_cancelled_fallback(
    monkeypatch,
    commit_result,
    cancel_result,
    expected_typed,
    expected_recorded,
    expected_cancel,
    expected_sound_count,
):
    bridge = FakeTsfBridge(commit_result=commit_result, cancel_result=cancel_result)
    typed = []
    finalized = []
    recorded = []
    played = []

    async def fake_polish(text, *, on_text=None, **_kwargs):
        assert text == "。ASR full text，"
        assert on_text is not None
        await on_text("LLM partial full text")
        return "LLM final full text"

    async def fake_type_result(text):
        typed.append(text)

    message = {
        "task_id": "task-1",
        "is_final": True,
        "text": "。ASR full text，",
        "time_start": 1.0,
        "time_submit": 2.0,
        "time_complete": 3.0,
        "source": "mic",
    }
    Cosmic.queue_out = cast(Any, OneMessageQueue(message))
    Cosmic.abandon_requested = False
    Cosmic.abandoned_task_ids.clear()
    Cosmic.active_task_id = None

    monkeypatch.setattr(pipeline, "get_tsf_speech_tip_bridge", lambda: bridge)
    monkeypatch.setattr(pipeline, "is_llm_polish_enabled", lambda: True)
    monkeypatch.setattr(pipeline, "should_polish_text", lambda _text: True)
    monkeypatch.setattr(pipeline, "polish_text", fake_polish)
    monkeypatch.setattr(pipeline, "regex_replace", lambda text: text)
    monkeypatch.setattr(pipeline.pangu, "spacing_text", lambda text: text)
    monkeypatch.setattr(pipeline, "type_result", fake_type_result)
    monkeypatch.setattr(pipeline, "record_finalized_text", finalized.append)
    monkeypatch.setattr(
        pipeline,
        "record_input_characters",
        lambda text, **_kwargs: recorded.append(text),
    )
    monkeypatch.setattr(
        "src.keyboard.play_music.play_completion_sound", lambda: played.append(True)
    )
    monkeypatch.setattr(pipeline.Config, "save_audio", False)
    monkeypatch.setattr(pipeline.Config, "save_markdown", False)

    asyncio.run(pipeline.recv_result())

    assert bridge.revisions == [
        ("task-1", "。ASR full text，", CompositionStyle.TRANSCRIPTION),
        ("task-1", "LLM partial full text", CompositionStyle.POLISHING),
    ]
    assert bridge.commits == [("task-1", "LLM final full text")]
    assert bridge.cancels == expected_cancel
    assert typed == expected_typed
    assert finalized == (["LLM final full text"] if expected_sound_count else [])
    assert recorded == expected_recorded
    assert len(played) == expected_sound_count


def test_full_text_asr_revision_is_not_sent_to_append_only_keyboard_fallback(
    monkeypatch,
):
    bridge = RejectingTsfBridge()
    recorded = []
    message = {
        "task_id": "task-realtime",
        "is_final": False,
        "is_transcript_delta": True,
        "transcript_revision_mode": "full_text",
        "text": "已稳定前缀加暂存尾部",
        "time_start": 1.0,
        "time_submit": 1.1,
        "time_complete": 1.2,
        "source": "mic",
        "stream": True,
    }
    Cosmic.queue_out = cast(Any, OneMessageQueue(message))
    Cosmic.abandon_requested = False
    Cosmic.abandoned_task_ids.clear()
    Cosmic.active_task_id = None
    Cosmic._transcript_had_deltas = False

    monkeypatch.setattr(pipeline, "get_tsf_speech_tip_bridge", lambda: bridge)
    monkeypatch.setattr(pipeline, "is_llm_polish_enabled", lambda: True)
    monkeypatch.setattr(pipeline, "regex_replace", lambda text: text)
    monkeypatch.setattr(
        pipeline,
        "record_input_characters",
        lambda text, **_kwargs: recorded.append(text),
    )

    asyncio.run(pipeline.recv_result())

    assert bridge.revisions == [
        (
            "task-realtime",
            "已稳定前缀加暂存尾部",
            CompositionStyle.TRANSCRIPTION,
        )
    ]
    assert Cosmic._transcript_had_deltas is False
    assert recorded == []


def test_unconfirmed_revision_does_not_fall_back_while_composition_is_owned(
    monkeypatch,
):
    bridge = OwnedButUnconfirmedTsfBridge()
    typed = []
    message = {
        "task_id": "task-realtime",
        "is_final": False,
        "is_transcript_delta": True,
        "text": "临时文本",
        "time_start": 1.0,
        "time_submit": 1.1,
        "time_complete": 1.2,
        "source": "mic",
        "stream": True,
    }
    Cosmic.queue_out = cast(Any, OneMessageQueue(message))
    Cosmic.abandon_requested = False
    Cosmic.abandoned_task_ids.clear()
    Cosmic.active_task_id = None

    Cosmic._transcript_had_deltas = False

    monkeypatch.setattr(pipeline, "get_tsf_speech_tip_bridge", lambda: bridge)
    monkeypatch.setattr(pipeline, "is_llm_polish_enabled", lambda: True)
    monkeypatch.setattr(pipeline, "regex_replace", lambda text: text)
    monkeypatch.setattr("keyboard.write", typed.append)

    asyncio.run(pipeline.recv_result())

    assert bridge.revisions == [
        ("task-realtime", "临时文本", CompositionStyle.TRANSCRIPTION)
    ]
    assert typed == []


def test_polishing_never_sends_append_only_transcript_to_keyboard_fallback(
    monkeypatch,
):
    bridge = RejectingTsfBridge()
    typed = []
    message = {
        "task_id": "task-realtime",
        "is_final": False,
        "is_transcript_delta": True,
        "text": "说到一半的原文",
        "time_start": 1.0,
        "time_submit": 1.1,
        "time_complete": 1.2,
        "source": "mic",
        "stream": True,
    }
    Cosmic.queue_out = cast(Any, OneMessageQueue(message))
    Cosmic.abandon_requested = False
    Cosmic.abandoned_task_ids.clear()
    Cosmic.active_task_id = None
    Cosmic._transcript_had_deltas = False

    monkeypatch.setattr(pipeline, "get_tsf_speech_tip_bridge", lambda: bridge)
    monkeypatch.setattr(pipeline, "is_llm_polish_enabled", lambda: True)
    monkeypatch.setattr(pipeline, "regex_replace", lambda text: text)
    monkeypatch.setattr("keyboard.write", typed.append)

    asyncio.run(pipeline.recv_result())

    assert typed == []
    assert Cosmic._transcript_had_deltas is False


def test_confirmed_external_termination_rollback_allows_complete_final_fallback(
    monkeypatch,
):
    bridge = RejectingTsfBridge()
    bridge.termination_rollback_tasks.add("task-terminated")
    typed = []

    async def fake_polish(_text, **_kwargs):
        return "完整润色文本"

    async def fake_type_result(text):
        typed.append(text)

    message = _final_message("task-terminated")
    message["has_incremental_transcript"] = True
    message["stream"] = True
    _configure_final_message_test(monkeypatch, bridge, message, fake_polish)
    monkeypatch.setattr(pipeline, "type_result", fake_type_result)
    monkeypatch.setattr(
        pipeline, "record_input_characters", lambda _text, **_kwargs: None
    )
    monkeypatch.setattr("src.keyboard.play_music.play_completion_sound", lambda: None)

    asyncio.run(pipeline.recv_result())

    assert typed == ["完整润色文本"]


def test_failed_external_termination_rollback_suppresses_duplicate_final_fallback(
    monkeypatch,
):
    bridge = RejectingTsfBridge()
    bridge.failed_termination_rollback_tasks.add("task-terminated")
    typed = []

    async def fake_polish(_text, **_kwargs):
        return "完整润色文本"

    async def fake_type_result(text):
        typed.append(text)

    message = _final_message("task-terminated")
    _configure_final_message_test(monkeypatch, bridge, message, fake_polish)
    monkeypatch.setattr(pipeline, "type_result", fake_type_result)
    monkeypatch.setattr("src.keyboard.play_music.play_completion_sound", lambda: None)

    asyncio.run(pipeline.recv_result())

    assert typed == []


def test_chatgpt_multiline_polish_cancels_then_pastes_final_once(monkeypatch):
    broker = ChatGptAckBroker()
    bridge = TsfSpeechTipBridge(broker)
    typed = []

    async def fake_polish(_text, *, on_text=None, **_kwargs):
        assert on_text is not None
        await on_text("第一段\n1. 列表")
        return "第一段\n1. 完整列表"

    async def fake_type_result(text):
        typed.append(text)

    _configure_final_message_test(
        monkeypatch, bridge, _final_message("task-chatgpt"), fake_polish
    )
    monkeypatch.setattr("src.tsf_ipc.bridge.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "src.tsf_ipc.bridge.Config.tsf_speech_tip_enabled", True, raising=False
    )
    monkeypatch.setattr(pipeline, "type_result", fake_type_result)
    monkeypatch.setattr(
        pipeline, "record_input_characters", lambda _text, **_kwargs: None
    )
    monkeypatch.setattr("src.keyboard.play_music.play_completion_sound", lambda: None)

    asyncio.run(pipeline.recv_result())

    assert typed == ["第一段\n1. 完整列表"]
    assert [frame.operation for frame in broker.frames] == [
        Operation.BEGIN,
        Operation.CANCEL,
    ]


def test_chatgpt_failed_multiline_cancel_never_revises_commits_or_pastes(
    monkeypatch,
):
    broker = ChatGptAckBroker(cancel_status=Status.EDIT_SESSION_FAILED)
    bridge = TsfSpeechTipBridge(broker)
    typed = []

    async def fake_polish(_text, *, on_text=None, **_kwargs):
        assert on_text is not None
        await on_text("第一段\n第二段")
        await on_text("第一段\n第二段继续")
        return "第一段\n最终文本"

    async def fake_type_result(text):
        typed.append(text)

    _configure_final_message_test(
        monkeypatch, bridge, _final_message("task-chatgpt"), fake_polish
    )
    monkeypatch.setattr("src.tsf_ipc.bridge.platform.system", lambda: "Windows")
    monkeypatch.setattr(
        "src.tsf_ipc.bridge.Config.tsf_speech_tip_enabled", True, raising=False
    )
    monkeypatch.setattr(pipeline, "type_result", fake_type_result)
    monkeypatch.setattr("src.keyboard.play_music.play_completion_sound", lambda: None)

    asyncio.run(pipeline.recv_result())

    assert typed == []
    assert broker.frames[0].operation == Operation.BEGIN
    assert all(frame.operation == Operation.CANCEL for frame in broker.frames[1:])
    assert Operation.REVISE not in [frame.operation for frame in broker.frames]
    assert Operation.COMMIT not in [frame.operation for frame in broker.frames]


def test_fast_polish_chunks_coalesce_behind_slow_ack_and_flush_before_commit(
    monkeypatch,
):
    class SlowPolishAckBridge(FakeTsfBridge):
        def __init__(self):
            super().__init__()
            self.first_polish_started = asyncio.Event()
            self.release_first_polish = asyncio.Event()
            self.order = []
            self.polish_revisions = 0

        async def begin_or_revise(
            self, task_id, text, style=CompositionStyle.TRANSCRIPTION
        ):
            self.owned_task = task_id
            self.revisions.append((task_id, text, style))
            self.order.append(("revision", text))
            if style == CompositionStyle.POLISHING:
                self.polish_revisions += 1
                if self.polish_revisions == 1:
                    self.first_polish_started.set()
                    await self.release_first_polish.wait()
            return True

        async def commit(self, task_id, text):
            self.order.append(("commit", text))
            return await super().commit(task_id, text)

    bridge = SlowPolishAckBridge()
    all_chunks_submitted = asyncio.Event()

    async def fake_polish(_text, *, on_text=None, **_kwargs):
        assert on_text is not None
        await on_text("首")
        await bridge.first_polish_started.wait()
        for text in ("首个", "首个快", "首个快速结果"):
            await on_text(text)
        all_chunks_submitted.set()
        return "首个快速结果"

    _configure_final_message_test(
        monkeypatch, bridge, _final_message("task-slow-ack"), fake_polish
    )
    monkeypatch.setattr(
        pipeline, "record_input_characters", lambda _text, **_kwargs: None
    )
    monkeypatch.setattr("src.keyboard.play_music.play_completion_sound", lambda: None)

    async def exercise():
        receive_task = asyncio.create_task(pipeline.recv_result())
        await all_chunks_submitted.wait()
        assert [item[1] for item in bridge.order] == ["ASR full text", "首"]
        bridge.release_first_polish.set()
        await receive_task
        leaked = [
            task
            for task in asyncio.all_tasks()
            if task is not asyncio.current_task()
            and task.get_name().startswith("tsf_revision_coalescer:")
        ]
        assert leaked == []

    asyncio.run(exercise())

    assert [
        text
        for _, text, style in bridge.revisions
        if style == CompositionStyle.POLISHING
    ] == [
        "首",
        "首个快速结果",
    ]
    assert bridge.order[-1] == ("commit", "首个快速结果")


def _configure_final_message_test(monkeypatch, bridge, message, fake_polish):
    Cosmic.queue_out = cast(Any, OneMessageQueue(message))
    Cosmic.abandon_requested = False
    Cosmic.abandoned_task_ids.clear()
    Cosmic.active_task_id = None

    monkeypatch.setattr(pipeline, "get_tsf_speech_tip_bridge", lambda: bridge)
    monkeypatch.setattr(pipeline, "is_llm_polish_enabled", lambda: True)
    monkeypatch.setattr(pipeline, "should_polish_text", lambda _text: True)
    monkeypatch.setattr(pipeline, "polish_text", fake_polish)
    monkeypatch.setattr(pipeline, "regex_replace", lambda text: text)
    monkeypatch.setattr(pipeline.pangu, "spacing_text", lambda text: text)
    monkeypatch.setattr(pipeline, "record_finalized_text", lambda _text: None)
    monkeypatch.setattr(pipeline.Config, "save_audio", False)
    monkeypatch.setattr(pipeline.Config, "save_markdown", False)


def _final_message(task_id):
    return {
        "task_id": task_id,
        "is_final": True,
        "text": "ASR full text",
        "time_start": 1.0,
        "time_submit": 2.0,
        "time_complete": 3.0,
        "source": "mic",
    }


def test_abandon_while_polishing_cancels_tsf_before_clearing_task(monkeypatch):
    bridge = FakeTsfBridge()
    original_cancel = bridge.cancel

    async def checked_cancel(task_id=None):
        assert Cosmic.abandon_requested is True
        assert Cosmic.active_task_id == task_id
        return await original_cancel(task_id)

    monkeypatch.setattr(bridge, "cancel", checked_cancel)

    async def fake_polish(_text, **_kwargs):
        Cosmic.abandon_requested = True
        raise asyncio.CancelledError

    _configure_final_message_test(
        monkeypatch, bridge, _final_message("task-polish-cancel"), fake_polish
    )

    asyncio.run(pipeline.recv_result())

    assert bridge.cancels == ["task-polish-cancel"]
    assert Cosmic.abandon_requested is False
    assert Cosmic.active_task_id is None


def test_abandon_during_final_spacing_cancels_tsf(monkeypatch):
    bridge = FakeTsfBridge()

    async def fake_polish(text, **_kwargs):
        return text

    def abandon_during_spacing(text):
        Cosmic.abandoned_task_ids.add("task-spacing-cancel")
        return text

    _configure_final_message_test(
        monkeypatch, bridge, _final_message("task-spacing-cancel"), fake_polish
    )
    monkeypatch.setattr(pipeline.pangu, "spacing_text", abandon_during_spacing)

    asyncio.run(pipeline.recv_result())

    assert bridge.cancels == ["task-spacing-cancel"]
    assert "task-spacing-cancel" not in Cosmic.abandoned_task_ids
    assert Cosmic.active_task_id is None


def test_receive_loop_exception_cancels_owned_tsf_composition(monkeypatch):
    bridge = FakeTsfBridge()

    async def fake_polish(text, **_kwargs):
        return text

    _configure_final_message_test(
        monkeypatch, bridge, _final_message("task-exception"), fake_polish
    )

    def fail_regex(_text):
        raise RuntimeError("formatting failed")

    monkeypatch.setattr(pipeline, "regex_replace", fail_regex)

    asyncio.run(pipeline.recv_result())

    assert bridge.cancels == ["task-exception"]
    assert Cosmic.active_task_id is None


def test_receive_loop_cancellation_cancels_owned_tsf_composition(monkeypatch):
    bridge = FakeTsfBridge()
    queue = MessageThenWaitQueue(
        {
            "task_id": "task-loop-cancel",
            "is_final": False,
            "is_transcript_delta": True,
            "transcript_revision_mode": "full_text",
            "text": "partial text",
            "time_submit": 1.0,
            "time_complete": 1.1,
            "stream": True,
        }
    )
    Cosmic.queue_out = cast(Any, queue)
    Cosmic.abandon_requested = False
    Cosmic.abandoned_task_ids.clear()
    Cosmic.active_task_id = None

    monkeypatch.setattr(pipeline, "get_tsf_speech_tip_bridge", lambda: bridge)
    monkeypatch.setattr(pipeline, "is_llm_polish_enabled", lambda: True)
    monkeypatch.setattr(pipeline, "regex_replace", lambda text: text)

    async def run_and_cancel():
        receive_task = asyncio.create_task(pipeline.recv_result())
        await queue.waiting.wait()
        receive_task.cancel()
        try:
            await receive_task
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("recv_result cancellation was suppressed")

    asyncio.run(run_and_cancel())

    assert bridge.cancels == ["task-loop-cancel"]
    assert Cosmic.active_task_id is None
