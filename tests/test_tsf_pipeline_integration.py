import asyncio

from src.infra.cosmic import Cosmic
from src.pipeline import recv_result as pipeline


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


class FakeTsfBridge:
    def __init__(self):
        self.owned_task = None
        self.revisions = []
        self.commits = []

    async def begin_or_revise(self, task_id, text):
        self.owned_task = task_id
        self.revisions.append((task_id, text))
        return True

    def owns_task(self, task_id):
        return task_id == self.owned_task

    async def commit(self, task_id, text):
        self.commits.append((task_id, text))
        self.owned_task = None
        return True

    async def cancel(self, task_id=None):
        self.owned_task = None
        return True


class RejectingTsfBridge(FakeTsfBridge):
    async def begin_or_revise(self, task_id, text):
        self.revisions.append((task_id, text))
        return False


def test_final_asr_and_llm_full_text_revisions_commit_without_legacy_paste(monkeypatch):
    bridge = FakeTsfBridge()
    typed = []
    finalized = []
    recorded = []

    async def fake_polish(text, *, on_text=None, **_kwargs):
        assert text == "ASR full text"
        assert on_text is not None
        await on_text("LLM partial full text")
        return "LLM final full text"

    async def fake_type_result(text):
        typed.append(text)

    message = {
        "task_id": "task-1",
        "is_final": True,
        "text": "ASR full text",
        "time_start": 1.0,
        "time_submit": 2.0,
        "time_complete": 3.0,
        "source": "mic",
    }
    Cosmic.queue_out = OneMessageQueue(message)
    Cosmic.abandon_requested = False
    Cosmic.abandoned_task_ids.clear()
    Cosmic.active_task_id = None

    monkeypatch.setattr(pipeline, "get_tsf_speech_tip_bridge", lambda: bridge)
    monkeypatch.setattr(pipeline, "is_llm_polish_enabled", lambda: True)
    monkeypatch.setattr(pipeline, "should_polish_text", lambda _text: True)
    monkeypatch.setattr(pipeline, "polish_text", fake_polish)
    monkeypatch.setattr(pipeline, "strip_punc", lambda text: text)
    monkeypatch.setattr(pipeline, "regex_replace", lambda text: text)
    monkeypatch.setattr(pipeline.pangu, "spacing_text", lambda text: text)
    monkeypatch.setattr(pipeline, "type_result", fake_type_result)
    monkeypatch.setattr(pipeline, "record_finalized_text", finalized.append)
    monkeypatch.setattr(
        pipeline,
        "record_input_characters",
        lambda text, **_kwargs: recorded.append(text),
    )
    monkeypatch.setattr("src.keyboard.play_music.play_completion_sound", lambda: None)
    monkeypatch.setattr(pipeline.Config, "save_audio", False)
    monkeypatch.setattr(pipeline.Config, "save_markdown", False)

    asyncio.run(pipeline.recv_result())

    assert bridge.revisions == [
        ("task-1", "ASR full text"),
        ("task-1", "LLM partial full text"),
    ]
    assert bridge.commits == [("task-1", "LLM final full text")]
    assert typed == []
    assert finalized == ["LLM final full text"]
    assert recorded == ["LLM final full text"]


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
    Cosmic.queue_out = OneMessageQueue(message)
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

    assert bridge.revisions == [("task-realtime", "已稳定前缀加暂存尾部")]
    assert Cosmic._transcript_had_deltas is False
    assert recorded == []
