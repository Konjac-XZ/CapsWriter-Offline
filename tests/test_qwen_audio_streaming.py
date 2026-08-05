import asyncio
import io
import json
import time
from types import SimpleNamespace
from typing import Any

import numpy as np
import dashscope

from src.infra.cosmic import Cosmic
from src.transcribe.providers import QwenAudioProvider
from src.transcribe.qwen_audio import qwen_audio_transcribe_sdk as streaming


class FakeResult:
    def __init__(self, sentence=None, message=""):
        self._sentence = sentence or {}
        self.message = message

    def get_sentence(self):
        return self._sentence


class FakeRecognition:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.start_kwargs = None
        self.frames = []
        self.stopped = False
        self.stop_hook = None
        self.__class__.instances.append(self)

    def start(self, **kwargs):
        self.start_kwargs = kwargs
        self.callback.on_open()

    def send_audio_frame(self, frame):
        self.frames.append(bytes(frame))

    def stop(self):
        self.stopped = True
        if self.stop_hook:
            self.stop_hook(self.callback)
        self.callback.on_complete()
        self.callback.on_close()

    def get_last_request_id(self):
        return "req-stream"

    def get_first_package_delay(self):
        return 42

    def get_last_package_delay(self):
        return 84


def _configure_streaming(monkeypatch):
    FakeRecognition.instances.clear()
    monkeypatch.setattr(streaming, "get_api_key", lambda: "sk-test")
    monkeypatch.setattr(
        streaming,
        "get_realtime_url",
        lambda: "wss://ws-test.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference",
    )
    monkeypatch.setattr(
        streaming,
        "get_realtime_model",
        lambda: "qwen-audio-3.0-asr-flash-streaming",
    )
    monkeypatch.setattr(streaming, "get_realtime_format", lambda: "pcm")
    monkeypatch.setattr(streaming, "get_realtime_sample_rate", lambda: 16000)
    monkeypatch.setattr(streaming, "get_realtime_chunk_ms", lambda: 100)
    monkeypatch.setattr(streaming, "get_realtime_timeout_seconds", lambda: 1.0)
    monkeypatch.setattr(streaming, "get_workspace_id", lambda: "ws-test")
    monkeypatch.setattr(streaming, "should_log_request_payload", lambda: False)
    monkeypatch.setattr(streaming, "should_emit_realtime_deltas", lambda: True)
    monkeypatch.setattr(
        streaming,
        "build_recognition_options",
        lambda: {
            "language_hints": ["zh", "en"],
            "vocabulary": {"QEMU": 4},
            "semantic_punctuation_enabled": False,
            "max_sentence_silence": 1300,
            "multi_threshold_mode_enabled": False,
            "heartbeat": False,
        },
    )


def test_realtime_url_prefers_explicit_then_workspace_then_public(monkeypatch):
    values: dict[str, Any] = {
        "realtime_url": "wss://override.example/inference/",
        "workspace_id": "ws-test",
        "region": "cn-beijing",
    }
    monkeypatch.setattr(
        streaming,
        "ps_get_str",
        lambda key, **kwargs: values.get(key, kwargs.get("default")),
    )
    assert streaming.get_realtime_url() == "wss://override.example/inference"

    values["realtime_url"] = None
    assert streaming.get_realtime_url() == (
        "wss://ws-test.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
    )

    values["workspace_id"] = None
    values["region"] = "singapore"
    assert streaming.get_realtime_url() == (
        "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
    )


def test_provider_streaming_support_follows_realtime_switch(monkeypatch):
    monkeypatch.setattr(streaming, "should_use_realtime", lambda: True)
    assert QwenAudioProvider().supports_streaming_input() is True

    monkeypatch.setattr(streaming, "should_use_realtime", lambda: False)
    assert QwenAudioProvider().supports_streaming_input() is False


def test_sdk_start_passes_context_hotwords_and_official_model(monkeypatch):
    _configure_streaming(monkeypatch)
    request_context = SimpleNamespace(asr_history=["上一条输入"])

    async def fake_capture(self):
        return request_context

    monkeypatch.setattr(
        streaming.QwenAudioStreamingSession,
        "_capture_request_context",
        fake_capture,
    )
    monkeypatch.setattr(
        streaming,
        "build_asr_context_messages",
        lambda context: [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": context.asr_history[0]}],
            }
        ],
    )

    async def run_case():
        session = streaming.QwenAudioStreamingSession(
            "task",
            1.0,
            recognition_factory=FakeRecognition,
        )
        await session.start()
        await session.cancel()

    asyncio.run(run_case())

    recognition = FakeRecognition.instances[0]
    assert recognition.kwargs["model"] == "qwen-audio-3.0-asr-flash-streaming"
    assert recognition.kwargs["format"] == "pcm"
    assert recognition.kwargs["sample_rate"] == 16000
    assert recognition.kwargs["workspace"] == "ws-test"
    assert recognition.kwargs["api_key"] == "sk-test"
    assert recognition.kwargs["vocabulary"] == {"QEMU": 4}
    assert recognition.kwargs["language_hints"] == ["zh", "en"]
    assert recognition.start_kwargs == {
        "raw_input": {
            "context": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "上一条输入"}],
                }
            ]
        }
    }
    assert dashscope.base_websocket_api_url == (
        "wss://ws-test.cn-beijing.maas.aliyuncs.com/api-ws/v1/inference"
    )


def test_pcm_is_coalesced_to_100ms_and_tail_is_flushed(monkeypatch):
    _configure_streaming(monkeypatch)

    async def fake_capture(self):
        return None

    monkeypatch.setattr(
        streaming.QwenAudioStreamingSession,
        "_capture_request_context",
        fake_capture,
    )

    async def run_case():
        Cosmic.queue_out = asyncio.Queue()
        session = streaming.QwenAudioStreamingSession(
            "task",
            1.0,
            recognition_factory=FakeRecognition,
        )
        await session.start()
        half_chunk = np.full((2400, 2), 0.25, dtype=np.float32)
        await session.send_audio(half_chunk)
        assert FakeRecognition.instances[0].frames == []
        await session.send_audio(half_chunk)
        assert [len(frame) for frame in FakeRecognition.instances[0].frames] == [3200]
        await session.send_audio(np.full((1200, 1), 0.25, dtype=np.float32))
        return await session.finish()

    _text, status, _submit, _complete, meta = asyncio.run(run_case())

    recognition = FakeRecognition.instances[0]
    assert [len(frame) for frame in recognition.frames] == [3200, 800]
    assert recognition.stopped is True
    assert status == 204
    assert meta["audio_bytes_sent"] == 4000


def test_sdk_callbacks_replace_partial_text_and_accumulate_sentences(monkeypatch):
    _configure_streaming(monkeypatch)

    async def fake_capture(self):
        return None

    monkeypatch.setattr(
        streaming.QwenAudioStreamingSession,
        "_capture_request_context",
        fake_capture,
    )

    def stop_hook(callback):
        callback.on_event(
            FakeResult({"sentence_id": 1, "text": "Hel", "end_time": None})
        )
        callback.on_event(
            FakeResult({"sentence_id": 1, "text": "Hello", "end_time": 500})
        )
        callback.on_event(
            FakeResult(
                {"sentence_id": 1, "text": "must not revise final", "end_time": 600}
            )
        )
        callback.on_event(
            FakeResult({"sentence_id": 0, "text": "ignored", "heartbeat": True})
        )
        callback.on_event(
            FakeResult({"sentence_id": 2, "text": "world", "end_time": 900})
        )

    async def run_case():
        Cosmic.queue_out = asyncio.Queue()
        session = streaming.QwenAudioStreamingSession(
            "task",
            1.0,
            recognition_factory=FakeRecognition,
        )
        await session.start()
        FakeRecognition.instances[0].stop_hook = stop_hook
        result = await session.finish()
        deltas = []
        while not Cosmic.queue_out.empty():
            deltas.append((await Cosmic.queue_out.get())["text"])
        return result, deltas

    result, deltas = asyncio.run(run_case())

    text, status, _submit, _complete, meta = result
    assert text == "Hello world"
    assert status == 200
    assert deltas == ["Hel", "Hello", "Hello world"]
    assert meta["emitted_deltas"] is True
    assert meta["request_id"] == "req-stream"
    assert meta["first_package_delay_ms"] == 42
    assert meta["last_package_delay_ms"] == 84


def test_context_capture_timeout_does_not_delay_stream_start(monkeypatch):
    _configure_streaming(monkeypatch)
    monkeypatch.setattr(streaming, "should_use_asr_context", lambda: True)
    monkeypatch.setattr(
        streaming,
        "get_asr_context_capture_timeout_seconds",
        lambda: 0.01,
    )

    async def slow_capture():
        await asyncio.sleep(1)
        return SimpleNamespace(asr_history=["too late"])

    monkeypatch.setattr(
        "src.polish.llm_polish.prefetch_request_context",
        slow_capture,
    )

    async def run_case():
        session = streaming.QwenAudioStreamingSession(
            "task",
            1.0,
            recognition_factory=FakeRecognition,
        )
        started = time.monotonic()
        await session.start()
        elapsed = time.monotonic() - started
        timed_out = session._context_timed_out
        await session.cancel()
        return elapsed, timed_out

    elapsed, timed_out = asyncio.run(run_case())

    assert elapsed < 0.2
    assert timed_out is True
    assert FakeRecognition.instances[0].start_kwargs == {}


def test_debug_config_keeps_context_and_hotwords_but_omits_secrets_and_audio(
    monkeypatch,
):
    _configure_streaming(monkeypatch)
    monkeypatch.setattr(streaming, "get_api_key", lambda: "sk-never-log-this")
    monkeypatch.setattr(streaming, "should_log_request_payload", lambda: True)
    logged = []
    monkeypatch.setattr(
        streaming.console, "print", lambda *args, **kwargs: logged.append(args[0])
    )

    async def fake_capture(self):
        return SimpleNamespace(asr_history=["QEMU context"])

    monkeypatch.setattr(
        streaming.QwenAudioStreamingSession,
        "_capture_request_context",
        fake_capture,
    )
    monkeypatch.setattr(
        streaming,
        "build_asr_context_messages",
        lambda _context: [
            {
                "role": "user",
                "content": [{"type": "input_text", "text": "QEMU context"}],
            }
        ],
    )

    async def run_case():
        session = streaming.QwenAudioStreamingSession(
            "task",
            1.0,
            recognition_factory=FakeRecognition,
        )
        await session.start()
        await session.send_audio(np.full((4800, 1), 0.25, dtype=np.float32))
        await session.cancel()

    asyncio.run(run_case())

    payload_text = "\n".join(logged)
    payload = json.loads(payload_text.split("\n", 1)[1])
    assert payload["input"]["context"][0]["content"][0]["text"] == "QEMU context"
    assert payload["parameters"]["vocabulary"] == {"QEMU": 4}
    assert "sk-never-log-this" not in payload_text
    assert "input_audio" not in payload_text
    assert "audio_bytes" not in payload_text
    assert "pcm" in payload_text


def test_sdk_stop_runs_without_blocking_event_loop(monkeypatch):
    _configure_streaming(monkeypatch)

    async def fake_capture(self):
        return None

    monkeypatch.setattr(
        streaming.QwenAudioStreamingSession,
        "_capture_request_context",
        fake_capture,
    )

    async def run_case():
        Cosmic.queue_out = asyncio.Queue()
        session = streaming.QwenAudioStreamingSession(
            "task",
            1.0,
            recognition_factory=FakeRecognition,
        )
        await session.start()
        recognition = FakeRecognition.instances[0]
        original_stop = recognition.stop

        def slow_stop():
            time.sleep(0.05)
            original_stop()

        recognition.stop = slow_stop
        finish_task = asyncio.create_task(session.finish())
        await asyncio.sleep(0.01)
        loop_was_responsive = not finish_task.done()
        await finish_task
        return loop_was_responsive

    assert asyncio.run(run_case()) is True


def test_sdk_error_returns_empty_result_for_http_fallback(monkeypatch):
    _configure_streaming(monkeypatch)

    async def fake_capture(self):
        return None

    monkeypatch.setattr(
        streaming.QwenAudioStreamingSession,
        "_capture_request_context",
        fake_capture,
    )

    async def run_case():
        Cosmic.queue_out = asyncio.Queue()
        session = streaming.QwenAudioStreamingSession(
            "task",
            1.0,
            recognition_factory=FakeRecognition,
        )
        await session.start()
        FakeRecognition.instances[0].stop_hook = lambda callback: callback.on_error(
            FakeResult(message="upstream failed")
        )
        return await session.finish()

    text, status, _submit, _complete, meta = asyncio.run(run_case())

    assert text == ""
    assert status == 502
    assert meta["error"] == "upstream failed"


def test_sdk_stop_timeout_returns_empty_result_for_http_fallback(monkeypatch):
    _configure_streaming(monkeypatch)
    monkeypatch.setattr(streaming, "get_realtime_timeout_seconds", lambda: 0.01)

    async def fake_capture(self):
        return None

    monkeypatch.setattr(
        streaming.QwenAudioStreamingSession,
        "_capture_request_context",
        fake_capture,
    )

    async def run_case():
        session = streaming.QwenAudioStreamingSession(
            "task",
            1.0,
            recognition_factory=FakeRecognition,
        )
        await session.start()
        recognition = FakeRecognition.instances[0]
        original_stop = recognition.stop

        def slow_stop():
            time.sleep(0.05)
            original_stop()

        recognition.stop = slow_stop
        return await session.finish()

    text, status, _submit, _complete, meta = asyncio.run(run_case())

    assert text == ""
    assert status == 502
    assert "timed out" in meta["error"]


def test_empty_streaming_result_continues_to_http_upload(monkeypatch):
    from src.audio import send_audio as send_audio_module
    from src.provider.domain import InputMode

    class EmptyStreamingSession:
        async def finish(self):
            now = time.time()
            return "", 204, now, now, {"streaming": True}

    async def fake_gather(_task_id):
        now = time.time()
        return (
            np.zeros((4800, 1), dtype=np.float32),
            0.1,
            now - 0.1,
            now,
            now,
            None,
            EmptyStreamingSession(),
            SimpleNamespace(input_modes={InputMode.FILE_UPLOAD}),
        )

    async def fake_prefetch():
        return None

    uploaded = []

    async def fake_submit(**kwargs):
        uploaded.append(kwargs)
        return True

    monkeypatch.setattr(send_audio_module, "_gather_audio_once", fake_gather)
    monkeypatch.setattr(
        send_audio_module, "_cache_recording_for_retry", lambda **_kwargs: None
    )
    monkeypatch.setattr(
        send_audio_module,
        "preprocess_audio",
        lambda audio: (audio, 16000),
    )
    monkeypatch.setattr(
        send_audio_module,
        "make_audio_payload",
        lambda *_args: asyncio.sleep(
            0,
            result=(io.BytesIO(b"http-audio"), "audio/wav", 0.0, 16000, 1),
        ),
    )
    monkeypatch.setattr(send_audio_module, "_submit_payload", fake_submit)
    monkeypatch.setattr(send_audio_module, "prefetch_request_context", fake_prefetch)
    Cosmic.abandon_requested = False
    Cosmic.abandoned_task_ids = set()

    asyncio.run(send_audio_module.send_audio())

    assert len(uploaded) == 1
    assert uploaded[0]["payload_mime"] == "audio/wav"
