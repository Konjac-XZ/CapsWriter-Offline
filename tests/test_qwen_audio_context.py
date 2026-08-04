import asyncio
import io
import time
from types import SimpleNamespace

from src.audio import send_audio
from src.polish import llm_polish
from src.polish import context_providers
from src.polish.textbox_context import TextBoxContext
from src.transcribe.qwen_audio import qwen_audio_transcribe_http as qwen_audio


def _enable_context(monkeypatch):
    monkeypatch.setattr(qwen_audio, "should_use_asr_context", lambda: True)
    monkeypatch.setattr(qwen_audio, "should_use_asr_history", lambda: True)
    monkeypatch.setattr(qwen_audio, "get_asr_history_max_messages", lambda: 4)
    monkeypatch.setattr(qwen_audio, "get_asr_history_max_chars", lambda: 400)
    monkeypatch.setattr(qwen_audio, "should_use_asr_textbox", lambda: True)
    monkeypatch.setattr(qwen_audio, "get_asr_textbox_max_chars", lambda: 400)


def test_asr_context_uses_four_history_messages_and_one_textbox(monkeypatch):
    _enable_context(monkeypatch)
    textbox = TextBoxContext(
        text="A" * 300 + "光标附近术语 ProtocolGuard" + "B" * 300,
        source="test",
        caret_offset=320,
    )
    context = SimpleNamespace(
        asr_history=[f"history-{index}-" + "x" * 500 for index in range(5)],
        captured_textbox_context=textbox,
        vision_context="不应发送的视觉摘要",
        prompt="不应发送的润色提示词",
    )

    messages = qwen_audio.build_asr_context_messages(context)

    assert len(messages) == 5
    texts = [message["content"][0]["text"] for message in messages]
    assert texts[0].startswith("history-1-")
    assert texts[3].startswith("history-4-")
    assert "ProtocolGuard" in texts[4]
    assert all(len(text) <= 400 for text in texts)
    assert all("视觉摘要" not in text for text in texts)
    assert all("润色提示词" not in text for text in texts)


def test_asr_context_without_caret_keeps_textbox_tail(monkeypatch):
    _enable_context(monkeypatch)
    monkeypatch.setattr(qwen_audio, "get_asr_textbox_max_chars", lambda: 20)
    context = SimpleNamespace(
        asr_history=[],
        captured_textbox_context=TextBoxContext(
            text="开头无关内容" + "x" * 30 + "结尾关键术语 MQTT",
            source="test",
            caret_offset=None,
        ),
    )

    messages = qwen_audio.build_asr_context_messages(context)

    assert len(messages) == 1
    assert messages[0]["content"][0]["text"].endswith("结尾关键术语 MQTT")


def test_asr_context_omits_invisible_only_textbox(monkeypatch):
    _enable_context(monkeypatch)
    context = SimpleNamespace(
        asr_history=[],
        captured_textbox_context=TextBoxContext(
            text="\u200b\ufeff",
            source="test",
        ),
    )

    assert qwen_audio.build_asr_context_messages(context) == []


def test_request_places_context_before_audio(monkeypatch):
    _enable_context(monkeypatch)
    monkeypatch.setattr(qwen_audio, "get_model", lambda: "qwen-audio-3.0-asr-flash")
    monkeypatch.setattr(qwen_audio, "get_language_hints", lambda: [])
    monkeypatch.setattr(qwen_audio, "get_vocabulary", lambda: {})
    monkeypatch.setattr(qwen_audio, "ps_get_int", lambda *args, **kwargs: None)
    monkeypatch.setattr(qwen_audio, "ps_get_str", lambda *args, **kwargs: None)
    context = SimpleNamespace(
        asr_history=["上一条输入"],
        captured_textbox_context=None,
    )

    body = qwen_audio.build_request_body("audio/wav", "YWJj", context)
    messages = body["input"]["messages"]

    assert messages[0]["content"][0] == {
        "type": "input_text",
        "text": "上一条输入",
    }
    assert messages[-1]["content"][0]["type"] == "input_audio"


def test_shared_capture_runs_for_qwen_when_llm_polish_is_disabled(monkeypatch):
    marker = object()
    monkeypatch.setattr(llm_polish, "is_llm_polish_enabled", lambda: False)
    monkeypatch.setattr(llm_polish, "_qwen_asr_context_enabled", lambda: True)
    monkeypatch.setattr(llm_polish, "_qwen_asr_textbox_enabled", lambda: False)
    monkeypatch.setattr(
        llm_polish,
        "_prepare_polish_request_context",
        lambda **_kwargs: marker,
    )

    assert asyncio.run(llm_polish.prefetch_request_context()) is marker


def test_prefetch_uses_context_provider_result(monkeypatch):
    marker = object()
    captured = TextBoxContext(
        text="TSF 光标上下文",
        source="tsf",
        caret_offset=3,
    )
    prepare_options = {}

    class Registry:
        async def capture(self, _options):
            return captured

    def prepare(**kwargs):
        prepare_options.update(kwargs)
        return marker

    monkeypatch.setattr(llm_polish, "is_llm_polish_enabled", lambda: True)
    monkeypatch.setattr(llm_polish, "_qwen_asr_context_enabled", lambda: False)
    monkeypatch.setattr(llm_polish, "_qwen_asr_textbox_enabled", lambda: False)
    monkeypatch.setattr(
        llm_polish,
        "_cfg",
        lambda: {"textbox_context": {"enabled": True}},
    )
    monkeypatch.setattr(llm_polish, "_prepare_polish_request_context", prepare)
    monkeypatch.setattr(
        context_providers,
        "DEFAULT_CONTEXT_PROVIDER_REGISTRY",
        Registry(),
    )

    assert asyncio.run(llm_polish.prefetch_request_context()) is marker
    assert prepare_options["captured_textbox_context"] is captured
    assert prepare_options["textbox_context_prepared"] is True


def test_shared_capture_keeps_asr_textbox_separate_from_polish_toggle(monkeypatch):
    captured = TextBoxContext(text="光标附近文本", source="test", caret_offset=3)
    capture_options = {}
    monkeypatch.setattr(
        llm_polish,
        "_cfg",
        lambda: {
            "enabled": False,
            "textbox_context": {
                "enabled": False,
                "max_chars": 100,
                "clipboard_fallback_enabled": False,
            },
            "history": {"enabled": False},
        },
    )
    monkeypatch.setattr(llm_polish, "_qwen_asr_textbox_enabled", lambda: True)

    def capture_textbox(**kwargs):
        capture_options.update(kwargs)
        return captured

    monkeypatch.setattr(llm_polish, "get_active_textbox_context", capture_textbox)
    monkeypatch.setattr(llm_polish, "get_recent_vision_context_summary", lambda: None)
    monkeypatch.setattr(llm_polish, "get_finalized_history", lambda: [])
    monkeypatch.setattr(llm_polish, "get_asr_finalized_history", lambda: ["ASR 历史"])
    monkeypatch.setattr(llm_polish, "_get_env", lambda *args, **kwargs: None)

    context = llm_polish._prepare_polish_request_context()

    assert context.captured_textbox_context is captured
    assert context.textbox_context is None
    assert capture_options["clipboard_fallback_enabled"] is False
    assert context.history == []
    assert context.asr_history == ["ASR 历史"]


def test_prepared_invisible_context_is_discarded(monkeypatch):
    captured = TextBoxContext(text="\u200b\ufeff", source="tsf", caret_offset=1)
    monkeypatch.setattr(
        llm_polish,
        "_cfg",
        lambda: {
            "enabled": True,
            "textbox_context": {"enabled": True, "max_chars": 100},
            "history": {"enabled": False},
        },
    )
    monkeypatch.setattr(llm_polish, "_qwen_asr_textbox_enabled", lambda: False)
    monkeypatch.setattr(llm_polish, "get_recent_vision_context_summary", lambda: None)
    monkeypatch.setattr(llm_polish, "get_finalized_history", lambda: [])
    monkeypatch.setattr(llm_polish, "get_asr_finalized_history", lambda: [])
    monkeypatch.setattr(llm_polish, "_get_env", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        "src.infra.user_lexicon.get_lexicon_user_message",
        lambda: None,
    )

    context = llm_polish._prepare_polish_request_context(
        captured_textbox_context=captured,
        textbox_context_prepared=True,
    )

    assert context.captured_textbox_context is None
    assert context.textbox_context is None
    assert context.textbox_context_has_position is False


def test_asr_history_records_even_when_polish_history_is_disabled(monkeypatch):
    llm_polish.clear_finalized_history()
    monkeypatch.setattr(
        llm_polish,
        "_cfg",
        lambda: {"history": {"enabled": False, "max_size": 5}},
    )
    monkeypatch.setattr(llm_polish, "_qwen_asr_history_settings", lambda: (True, 4))

    llm_polish.record_finalized_text("第一条")
    llm_polish.record_finalized_text("第二条")

    assert llm_polish.get_asr_finalized_history() == ["第一条", "第二条"]
    llm_polish.clear_finalized_history()


def test_asr_context_timeout_does_not_cancel_shared_capture(monkeypatch):
    monkeypatch.setattr(send_audio, "_is_qwen_audio_provider", lambda: True)
    monkeypatch.setattr(qwen_audio, "should_use_asr_context", lambda: True)
    monkeypatch.setattr(
        qwen_audio,
        "get_asr_context_capture_timeout_seconds",
        lambda: 0.01,
    )

    async def run_case():
        async def slow_capture():
            await asyncio.sleep(1)
            return "context"

        task = asyncio.create_task(slow_capture())
        context, timed_out = await send_audio._await_qwen_asr_context(task)
        assert context is None
        assert timed_out is True
        assert task.cancelled() is False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(run_case())


def test_submit_payload_passes_shared_context_to_qwen(monkeypatch):
    marker = object()
    received = {}

    monkeypatch.setattr(send_audio, "_is_qwen_audio_provider", lambda: True)
    monkeypatch.setattr(
        send_audio,
        "_await_qwen_asr_context",
        lambda task: _resolved_context(task, marker),
    )
    monkeypatch.setattr(send_audio, "should_polish_text", lambda text: False)
    monkeypatch.setattr(send_audio, "get_incremental_results_flag", lambda: False)

    async def fake_transcribe_audio(*args, **kwargs):
        received["context"] = kwargs.get("request_context")
        now = time.time()
        return "识别结果", 200, now, now, {"http2": True}

    monkeypatch.setattr(send_audio, "transcribe_audio", fake_transcribe_audio)

    async def run_case():
        send_audio.Cosmic.abandon_requested = False
        send_audio.Cosmic.abandoned_task_ids.clear()
        send_audio.Cosmic.queue_out = asyncio.Queue()
        context_task = asyncio.create_task(asyncio.sleep(0, result=marker))
        submitted = await send_audio._submit_payload(
            payload_buf=io.BytesIO(b"wav"),
            payload_mime="audio/wav",
            task_id="context-test",
            time_start=time.time() - 1,
            record_stop=time.time(),
            t_finish_entry=time.time(),
            encode_ms=0.0,
            payload_sr=16000,
            payload_ch=1,
            duration=1.0,
            source="test",
            cache_retry_audio=False,
            polish_prefetch_task=context_task,
        )
        message = await send_audio.Cosmic.queue_out.get()
        return submitted, message

    submitted, message = asyncio.run(run_case())

    assert submitted is True
    assert received["context"] is marker
    assert message["text"] == "识别结果"


async def _resolved_context(task, expected):
    assert await task is expected
    return expected, False
