import asyncio

from src.transcribe.dashscope import settings
from src.transcribe.dashscope import dashscope_transcribe_ws as realtime
from src.infra.cosmic import Cosmic
from src.transcribe.providers import DashScopeProvider, make_provider


def test_realtime_model_defaults_from_qwen3_flash(monkeypatch):
    monkeypatch.setattr(settings, "ps_get_str", lambda key, **kwargs: None if key == "realtime_model" else "qwen3-asr-flash")

    assert settings.get_realtime_model() == "qwen3-asr-flash-realtime"


def test_realtime_parameters_include_pcm_defaults(monkeypatch):
    monkeypatch.setattr(settings, "get_realtime_format", lambda: "pcm")
    monkeypatch.setattr(settings, "get_realtime_sample_rate", lambda: 16000)
    monkeypatch.setattr(settings, "get_realtime_channels", lambda: 1)
    monkeypatch.setattr(settings, "get_language", lambda: "zh")
    monkeypatch.setattr(settings, "get_enable_lid", lambda: False)
    monkeypatch.setattr(settings, "get_enable_itn", lambda: True)
    monkeypatch.setattr(settings, "get_context_text", lambda: "CapsWriter")

    assert settings.build_realtime_parameters() == {
        "format": "pcm",
        "sample_rate": 16000,
        "language": "zh",
        "enable_itn": True,
        "context": "CapsWriter",
    }


def test_realtime_session_update_uses_manual_commit_by_default(monkeypatch):
    monkeypatch.setattr(settings, "get_realtime_format", lambda: "pcm")
    monkeypatch.setattr(settings, "get_realtime_sample_rate", lambda: 16000)
    monkeypatch.setattr(settings, "get_language", lambda: "zh")
    monkeypatch.setattr(settings, "get_context_text", lambda: "CapsWriter")
    monkeypatch.setattr(settings, "get_realtime_enable_vad", lambda: False)

    event = settings.build_realtime_session_update()

    assert event["type"] == "session.update"
    assert event["session"]["input_audio_format"] == "pcm"
    assert event["session"]["sample_rate"] == 16000
    assert event["session"]["turn_detection"] is None
    assert event["session"]["input_audio_transcription"] == {
        "language": "zh",
        "corpus": {"text": "CapsWriter"},
    }


def test_realtime_url_adds_model_query(monkeypatch):
    monkeypatch.setattr(settings, "get_realtime_url", lambda: "wss://dashscope.aliyuncs.com/api-ws/v1/realtime")
    monkeypatch.setattr(settings, "get_realtime_model", lambda: "qwen3-asr-flash-realtime")

    assert realtime.build_realtime_url().endswith("?model=qwen3-asr-flash-realtime")


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
        session = realtime.DashScopeRealtimeSession("task", 1.0)

        await session._emit_delta("累计全文")
        return await queue.get()

    message = asyncio.run(run_case())

    assert message["text"] == "累计全文"
    assert message["is_transcript_delta"] is True
    assert message["transcript_revision_mode"] == "full_text"


def test_realtime_finish_sends_commit_and_session_finish(monkeypatch):
    import asyncio

    sent = []

    class FakeWebSocket:
        async def send(self, payload):
            sent.append(payload)

        async def close(self):
            pass

    monkeypatch.setattr(settings, "get_realtime_enable_vad", lambda: False)
    monkeypatch.setattr(settings, "get_realtime_timeout_seconds", lambda: 1)
    monkeypatch.setattr(settings, "get_realtime_model", lambda: "qwen3-asr-flash-realtime")

    async def run_case():
        session = realtime.DashScopeRealtimeSession("task", 1.0)
        session._ws = FakeWebSocket()
        session._started = True
        session._done.set()

        await session.finish()

    asyncio.run(run_case())

    assert '"type": "input_audio_buffer.commit"' in sent[0]
    assert '"event_id": "event_commit_' in sent[0]
    assert '"type": "session.finish"' in sent[1]
    assert '"event_id": "event_finish_' in sent[1]


def test_realtime_finish_with_text_schedules_close_without_waiting(monkeypatch):
    import asyncio
    import contextlib

    sent = []
    closed = False

    class FakeWebSocket:
        async def send(self, payload):
            sent.append(payload)

        async def close(self):
            nonlocal closed
            await asyncio.sleep(10)
            closed = True

    monkeypatch.setattr(settings, "get_realtime_enable_vad", lambda: False)
    monkeypatch.setattr(settings, "get_realtime_timeout_seconds", lambda: 1)
    monkeypatch.setattr(settings, "get_realtime_model", lambda: "qwen3-asr-flash-realtime")
    monkeypatch.setattr(settings, "should_show_debug_logs", lambda: False)

    async def run_case():
        session = realtime.DashScopeRealtimeSession("task", 1.0)
        session._ws = FakeWebSocket()
        session._started = True
        session._done.set()
        session._final_text = "最终文本"

        result = await asyncio.wait_for(session.finish(), timeout=0.5)
        assert session._cleanup_task is not None
        session._cleanup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await session._cleanup_task
        return result

    text, status, *_ = asyncio.run(run_case())

    assert text == "最终文本"
    assert status == 200
    assert closed is False


def test_realtime_finish_empty_text_uses_204_status(monkeypatch):
    import asyncio

    sent = []

    class FakeWebSocket:
        async def send(self, payload):
            sent.append(payload)

        async def close(self):
            pass

    monkeypatch.setattr(settings, "get_realtime_enable_vad", lambda: False)
    monkeypatch.setattr(settings, "get_realtime_timeout_seconds", lambda: 1)
    monkeypatch.setattr(settings, "get_realtime_model", lambda: "qwen3-asr-flash-realtime")
    monkeypatch.setattr(settings, "should_show_debug_logs", lambda: False)

    async def run_case():
        session = realtime.DashScopeRealtimeSession("task", 1.0)
        session._ws = FakeWebSocket()
        session._started = True
        session._done.set()

        return await session.finish()

    text, status, *_ = asyncio.run(run_case())

    assert text == ""
    assert status == 204


def test_realtime_deltas_are_disabled_by_default(monkeypatch):
    monkeypatch.setattr(settings, "ps_get_bool", lambda *args, **kwargs: kwargs.get("default", False))

    assert settings.should_emit_realtime_deltas() is False


def test_realtime_finish_reports_delta_emission_state(monkeypatch):
    import asyncio

    sent = []

    class FakeWebSocket:
        async def send(self, payload):
            sent.append(payload)

        async def close(self):
            pass

    monkeypatch.setattr(settings, "get_realtime_enable_vad", lambda: False)
    monkeypatch.setattr(settings, "get_realtime_timeout_seconds", lambda: 1)
    monkeypatch.setattr(settings, "get_realtime_model", lambda: "qwen3-asr-flash-realtime")
    monkeypatch.setattr(settings, "should_show_debug_logs", lambda: False)

    async def run_case():
        session = realtime.DashScopeRealtimeSession("task", 1.0)
        session._ws = FakeWebSocket()
        session._started = True
        session._done.set()
        session._final_text = "最终文本"

        return await session.finish()

    text, status, _t_submit, _t_complete, meta = asyncio.run(run_case())

    assert text == "最终文本"
    assert status == 200
    assert meta["emitted_deltas"] is False


def test_dashscope_streaming_input_requires_realtime_enabled(monkeypatch):
    monkeypatch.setattr(settings, "should_use_realtime", lambda: True)

    assert DashScopeProvider().supports_streaming_input() is True

    monkeypatch.setattr(settings, "should_use_realtime", lambda: False)

    assert DashScopeProvider().supports_streaming_input() is False


def test_make_provider_supports_qwen_audio_legacy_name():
    assert isinstance(make_provider("qwen-audio-legacy"), DashScopeProvider)
