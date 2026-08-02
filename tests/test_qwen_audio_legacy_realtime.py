import asyncio
import base64
import threading
from typing import Any

import numpy as np
import pytest

from src.transcribe.qwen_audio_legacy import settings
from src.transcribe.qwen_audio_legacy import (
    qwen_audio_legacy_transcribe_sdk as realtime,
)
from src.infra.cosmic import Cosmic
from src.transcribe.providers import QwenAudioLegacyProvider, make_provider


def test_realtime_model_defaults_from_qwen3_flash(monkeypatch):
    monkeypatch.setattr(
        settings,
        "ps_get_str",
        lambda key, **kwargs: None if key == "realtime_model" else "qwen3-asr-flash",
    )

    assert settings.get_realtime_model() == "qwen3-asr-flash-realtime"


def test_realtime_url_leaves_model_query_to_official_sdk(monkeypatch):
    monkeypatch.setattr(
        settings,
        "get_realtime_url",
        lambda: "wss://dashscope.aliyuncs.com/api-ws/v1/realtime",
    )

    assert realtime.build_realtime_url() == (
        "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    )


def test_realtime_url_prefers_explicit_then_workspace_then_public(monkeypatch):
    values: dict[str, Any] = {
        "realtime_url": "wss://override.example/realtime",
        "workspace_id": "ws-test",
        "region": "cn-beijing",
    }
    monkeypatch.setattr(
        settings,
        "ps_get_str",
        lambda key, **kwargs: values.get(key, kwargs.get("default")),
    )
    assert settings.get_realtime_url() == "wss://override.example/realtime"

    values["realtime_url"] = None
    assert settings.get_realtime_url() == (
        "wss://ws-test.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime"
    )

    values["workspace_id"] = None
    values["region"] = "singapore"
    assert settings.get_realtime_url() == (
        "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
    )


def test_downmix_resample_to_pcm16_outputs_16k_little_endian():
    import numpy as np

    audio = np.ones((48000, 2), dtype=np.float32) * 0.5
    pcm = realtime._downmix_resample_to_pcm16(audio, 16000)

    assert len(pcm) == 16000 * 2
    assert pcm[:2] == (16383).to_bytes(2, byteorder="little", signed=True)


def test_extract_text_reads_qwen_realtime_transcript_events():
    message = {
        "type": "conversation.item.input_audio_transcription.completed",
        "item": {
            "content": [
                {
                    "type": "input_audio",
                    "transcript": "你好，CapsWriter。",
                }
            ]
        },
    }

    assert realtime._extract_text_fragment(message) == "你好，CapsWriter。"


def test_extract_text_reads_nested_response_output():
    message = {
        "type": "response.done",
        "response": {
            "output": [
                {
                    "content": [
                        {
                            "text": "嵌套文本",
                        }
                    ],
                }
            ],
        },
    }

    assert realtime._extract_text_fragment(message) == "嵌套文本"


def test_extract_text_combines_realtime_text_and_stash():
    message = {
        "type": "conversation.item.input_audio_transcription.text",
        "text": "",
        "stash": "临时识别",
    }

    assert realtime._extract_text_fragment(message) == "临时识别"


def test_extract_text_combines_finalized_prefix_and_revisable_stash():
    message = {
        "type": "conversation.item.input_audio_transcription.delta",
        "text": "这是已经稳定的",
        "stash": "暂存为本",
    }

    assert realtime._extract_text_fragment(message) == "这是已经稳定的暂存为本"


def test_realtime_delta_is_marked_as_full_text_revision(monkeypatch):
    async def run_case():
        queue = asyncio.Queue()
        monkeypatch.setattr(Cosmic, "queue_out", queue, raising=False)
        session = realtime.QwenAudioLegacyRealtimeSession("task", 1.0)

        await session._emit_delta("累计全文")
        return await queue.get()

    message = asyncio.run(run_case())

    assert message["text"] == "累计全文"
    assert message["is_transcript_delta"] is True
    assert message["transcript_revision_mode"] == "full_text"


def _configure_sdk_realtime(monkeypatch):
    monkeypatch.setattr(settings, "get_api_key", lambda: "sk-test")
    monkeypatch.setattr(settings, "get_workspace_id", lambda: "ws-test")
    monkeypatch.setattr(
        settings,
        "get_realtime_url",
        lambda: "wss://ws-test.cn-beijing.maas.aliyuncs.com/api-ws/v1/realtime",
    )
    monkeypatch.setattr(settings, "get_realtime_format", lambda: "pcm")
    monkeypatch.setattr(settings, "get_realtime_sample_rate", lambda: 16000)
    monkeypatch.setattr(settings, "get_language", lambda: "zh")
    monkeypatch.setattr(settings, "get_context_text", lambda: "CapsWriter")
    monkeypatch.setattr(settings, "get_realtime_enable_vad", lambda: False)
    monkeypatch.setattr(settings, "get_realtime_vad_threshold", lambda: 0.0)
    monkeypatch.setattr(settings, "get_realtime_vad_silence_ms", lambda: 400)
    monkeypatch.setattr(settings, "get_realtime_timeout_seconds", lambda: 0.2)
    monkeypatch.setattr(settings, "get_realtime_close_timeout_seconds", lambda: 0.2)
    monkeypatch.setattr(
        settings, "get_realtime_model", lambda: "qwen3-asr-flash-realtime"
    )
    monkeypatch.setattr(settings, "should_show_debug_logs", lambda: False)


class FakeOmniConversation:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.update_kwargs = None
        self.frames = []
        self.committed = False
        self.ended = False
        self.closed = False
        self.end_hook = None
        self.__class__.instances.append(self)

    def connect(self):
        self.callback.on_open()
        self.callback.on_event({"type": "session.created", "session": {"id": "sid"}})

    def update_session(self, **kwargs):
        self.update_kwargs = kwargs
        self.callback.on_event({"type": "session.updated"})

    def append_audio(self, audio_b64):
        self.frames.append(audio_b64)

    def commit(self):
        self.committed = True

    def end_session(self, timeout):
        self.ended = True
        if self.end_hook:
            self.end_hook(self.callback)
        self.callback.on_event({"type": "session.finished"})

    def close(self):
        self.closed = True
        self.callback.on_close(1000, "closed")

    def get_session_id(self):
        return "sid"

    def get_last_response_id(self):
        return "rid"

    def get_last_first_text_delay(self):
        return 35


def test_official_sdk_receives_connection_and_session_parameters(monkeypatch):
    _configure_sdk_realtime(monkeypatch)
    FakeOmniConversation.instances.clear()

    async def run_case():
        session = realtime.QwenAudioLegacyRealtimeSession(
            "task",
            1.0,
            conversation_factory=FakeOmniConversation,
        )
        await session.start()
        await session.cancel()

    asyncio.run(run_case())

    conversation = FakeOmniConversation.instances[0]
    assert conversation.kwargs["model"] == "qwen3-asr-flash-realtime"
    assert conversation.kwargs["api_key"] == "sk-test"
    assert conversation.kwargs["workspace"] == "ws-test"
    assert conversation.kwargs["url"].endswith("/api-ws/v1/realtime")
    params = conversation.update_kwargs["transcription_params"]
    assert params.language == "zh"
    assert params.sample_rate == 16000
    assert params.input_audio_format == "pcm"
    assert params.corpus_text == "CapsWriter"
    assert conversation.update_kwargs["enable_turn_detection"] is False


def test_official_sdk_streams_pcm_and_merges_cross_thread_revisions(monkeypatch):
    _configure_sdk_realtime(monkeypatch)
    FakeOmniConversation.instances.clear()
    monkeypatch.setattr(settings, "should_emit_realtime_deltas", lambda: True)

    callback_thread_ids = []

    def end_hook(callback):
        callback_thread_ids.append(threading.get_ident())
        callback.on_event(
            {
                "type": "conversation.item.input_audio_transcription.text",
                "text": "Hello",
                "stash": " wor",
            }
        )
        callback.on_event(
            {
                "type": "conversation.item.input_audio_transcription.text",
                "text": "Hello",
                "stash": " world",
            }
        )
        callback.on_event(
            {
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": "Hello world",
            }
        )

    async def run_case():
        Cosmic.queue_out = asyncio.Queue()
        main_thread_id = threading.get_ident()
        session = realtime.QwenAudioLegacyRealtimeSession(
            "task",
            1.0,
            conversation_factory=FakeOmniConversation,
        )
        await session.start()
        conversation = FakeOmniConversation.instances[0]
        conversation.end_hook = end_hook
        await session.send_audio(np.full((4800, 2), 0.25, dtype=np.float32))
        result = await session.finish()
        deltas = []
        while not Cosmic.queue_out.empty():
            deltas.append((await Cosmic.queue_out.get())["text"])
        return result, deltas, main_thread_id

    result, deltas, main_thread_id = asyncio.run(run_case())

    text, status, _submit, _complete, meta = result
    conversation = FakeOmniConversation.instances[0]
    assert len(base64.b64decode(conversation.frames[0])) == 3200
    assert conversation.committed is True
    assert conversation.ended is True
    assert conversation.closed is True
    assert callback_thread_ids[0] != main_thread_id
    assert deltas == ["Hello wor", "Hello world"]
    assert text == "Hello world"
    assert status == 200
    assert meta["via"] == "qwen-audio-legacy-sdk-omni-realtime"
    assert meta["session_id"] == "sid"
    assert meta["response_id"] == "rid"
    assert meta["first_text_delay_ms"] == 35


def test_official_sdk_empty_result_uses_http_fallback_status(monkeypatch):
    _configure_sdk_realtime(monkeypatch)
    FakeOmniConversation.instances.clear()
    monkeypatch.setattr(settings, "should_emit_realtime_deltas", lambda: False)

    async def run_case():
        session = realtime.QwenAudioLegacyRealtimeSession(
            "task",
            1.0,
            conversation_factory=FakeOmniConversation,
        )
        await session.start()
        return await session.finish()

    text, status, *_ = asyncio.run(run_case())

    assert text == ""
    assert status == 204


def test_official_sdk_session_error_aborts_start_for_http_fallback(monkeypatch):
    _configure_sdk_realtime(monkeypatch)

    class ErrorConversation(FakeOmniConversation):
        def update_session(self, **kwargs):
            self.update_kwargs = kwargs
            self.callback.on_event(
                {
                    "type": "error",
                    "error": {"code": "InvalidParameter", "message": "bad session"},
                }
            )

    async def run_case():
        session = realtime.QwenAudioLegacyRealtimeSession(
            "task",
            1.0,
            conversation_factory=ErrorConversation,
        )
        with pytest.raises(RuntimeError, match="bad session"):
            await session.start()

    asyncio.run(run_case())


def test_realtime_deltas_are_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        settings, "ps_get_bool", lambda *args, **kwargs: kwargs.get("default", False)
    )

    assert settings.should_emit_realtime_deltas() is False


def test_qwen_audio_legacy_streaming_input_requires_realtime_enabled(monkeypatch):
    monkeypatch.setattr(settings, "should_use_realtime", lambda: True)

    assert QwenAudioLegacyProvider().supports_streaming_input() is True

    monkeypatch.setattr(settings, "should_use_realtime", lambda: False)

    assert QwenAudioLegacyProvider().supports_streaming_input() is False


def test_make_provider_supports_qwen_audio_legacy_name():
    assert isinstance(make_provider("qwen-audio-legacy"), QwenAudioLegacyProvider)
