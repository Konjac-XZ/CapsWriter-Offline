from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Callable, Dict, Tuple, cast

import numpy as np

from src.infra.cosmic import Cosmic, console
from src.provider.provider_settings import (
    get_bool as ps_get_bool,
    get_int as ps_get_int,
    get_str as ps_get_str,
)
from src.transcribe.qwen_audio.qwen_audio_transcribe_http import (
    build_asr_context_messages,
    get_api_key,
    get_asr_context_capture_timeout_seconds,
    get_language_hints,
    get_vocabulary,
    request_body_for_debug,
    should_log_request_payload,
    should_use_asr_context,
)
from src.transcribe.streaming import StreamingTranscriptionSession


_DEFAULT_REALTIME_MODEL = "qwen-audio-3.0-asr-flash-streaming"
_DEFAULT_BEIJING_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
_DEFAULT_SINGAPORE_URL = "wss://dashscope-intl.aliyuncs.com/api-ws/v1/inference"
_STOP_EVENT = object()


def should_use_realtime() -> bool:
    return ps_get_bool("realtime", env="QWEN_AUDIO_3_REALTIME", default=True)


def should_emit_realtime_deltas() -> bool:
    return ps_get_bool(
        "realtime_emit_deltas",
        env="QWEN_AUDIO_3_REALTIME_EMIT_DELTAS",
        default=True,
    )


def get_realtime_model() -> str:
    return (
        ps_get_str(
            "realtime_model",
            env="QWEN_AUDIO_3_REALTIME_MODEL",
            default=_DEFAULT_REALTIME_MODEL,
        )
        or _DEFAULT_REALTIME_MODEL
    )


def get_realtime_format() -> str:
    return (
        ps_get_str(
            "realtime_format",
            env="QWEN_AUDIO_3_REALTIME_FORMAT",
            default="pcm",
        )
        or "pcm"
    ).strip().lower()


def get_realtime_sample_rate() -> int:
    value = ps_get_int(
        "realtime_sample_rate",
        env="QWEN_AUDIO_3_REALTIME_SAMPLE_RATE",
        default=16000,
    )
    return max(8000, min(48000, int(value or 16000)))


def get_realtime_chunk_ms() -> int:
    value = ps_get_int(
        "realtime_chunk_ms",
        env="QWEN_AUDIO_3_REALTIME_CHUNK_MS",
        default=100,
    )
    return max(20, min(500, int(value or 100)))


def get_realtime_timeout_seconds() -> float:
    raw = ps_get_str(
        "realtime_timeout_seconds",
        env="QWEN_AUDIO_3_REALTIME_TIMEOUT_SECONDS",
        default="30",
    )
    try:
        return max(1.0, min(120.0, float(raw or "30")))
    except Exception:
        return 30.0


def get_workspace_id() -> str | None:
    return ps_get_str(
        "workspace_id",
        env="DASHSCOPE_WORKSPACE_ID",
        default=None,
    )


def get_region() -> str:
    return (
        ps_get_str("region", env="DASHSCOPE_REGION", default="cn-beijing")
        or "cn-beijing"
    ).strip().lower()


def get_realtime_url() -> str:
    explicit = ps_get_str(
        "realtime_url",
        env="QWEN_AUDIO_3_REALTIME_URL",
        default=None,
    )
    if explicit:
        return explicit.rstrip("/")

    region = get_region()
    workspace_id = get_workspace_id()
    if region in {"cn-beijing", "beijing"}:
        if workspace_id:
            return (
                f"wss://{workspace_id}.cn-beijing.maas.aliyuncs.com"
                "/api-ws/v1/inference"
            )
        return _DEFAULT_BEIJING_URL
    if region in {"ap-southeast-1", "singapore"}:
        if workspace_id:
            return (
                f"wss://{workspace_id}.ap-southeast-1.maas.aliyuncs.com"
                "/api-ws/v1/inference"
            )
        return _DEFAULT_SINGAPORE_URL
    raise ValueError("Qwen Audio 3 realtime region must be cn-beijing or ap-southeast-1")


def get_semantic_punctuation_enabled() -> bool:
    return ps_get_bool(
        "semantic_punctuation_enabled",
        env="QWEN_AUDIO_3_SEMANTIC_PUNCTUATION_ENABLED",
        default=False,
    )


def get_max_sentence_silence() -> int:
    value = ps_get_int(
        "max_sentence_silence",
        env="QWEN_AUDIO_3_MAX_SENTENCE_SILENCE",
        default=1300,
    )
    return max(200, min(6000, int(value or 1300)))


def get_multi_threshold_mode_enabled() -> bool:
    return ps_get_bool(
        "multi_threshold_mode_enabled",
        env="QWEN_AUDIO_3_MULTI_THRESHOLD_MODE_ENABLED",
        default=False,
    )


def get_heartbeat_enabled() -> bool:
    return ps_get_bool(
        "heartbeat",
        env="QWEN_AUDIO_3_HEARTBEAT",
        default=False,
    )


def build_recognition_options() -> dict[str, Any]:
    options: dict[str, Any] = {
        "semantic_punctuation_enabled": get_semantic_punctuation_enabled(),
        "max_sentence_silence": get_max_sentence_silence(),
        "multi_threshold_mode_enabled": get_multi_threshold_mode_enabled(),
        "heartbeat": get_heartbeat_enabled(),
    }
    language_hints = get_language_hints()
    if language_hints:
        options["language_hints"] = language_hints[:4]

    vocabulary = get_vocabulary()
    if vocabulary:
        options["vocabulary"] = vocabulary
    else:
        vocabulary_id = ps_get_str(
            "vocabulary_id",
            env="QWEN_AUDIO_3_VOCABULARY_ID",
            default=None,
        )
        if vocabulary_id:
            options["vocabulary_id"] = vocabulary_id
    return options


def downmix_resample_to_pcm16(audio_chunk: np.ndarray, target_sr: int) -> bytes:
    if audio_chunk.size == 0:
        return b""
    if audio_chunk.ndim == 2:
        mono = audio_chunk.mean(axis=1)
    else:
        mono = audio_chunk
    mono = np.nan_to_num(mono, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
    mono = np.clip(mono, -1.0, 1.0)

    if target_sr != 48000:
        if 48000 % target_sr == 0:
            mono = mono[:: 48000 // target_sr]
        else:
            src_pos = np.arange(len(mono), dtype=np.float64)
            dst_len = max(1, int(round(len(mono) * target_sr / 48000)))
            dst_pos = np.linspace(0, max(0, len(mono) - 1), dst_len)
            mono = np.interp(dst_pos, src_pos, mono).astype(np.float32, copy=False)

    return (mono * 32767.0).astype("<i2", copy=False).tobytes()


def join_transcript_segments(segments: list[str]) -> str:
    result = ""
    for raw_segment in segments:
        segment = str(raw_segment or "").strip()
        if not segment:
            continue
        if (
            result
            and result[-1].isascii()
            and result[-1].isalnum()
            and segment[0].isascii()
            and segment[0].isalnum()
        ):
            result += " "
        result += segment
    return result


def _error_message(value: Any) -> str:
    for name in ("message", "code", "request_id"):
        candidate = getattr(value, name, None)
        if candidate:
            return str(candidate)
    return str(value or "Qwen Audio 3 realtime SDK error")


class _RecognitionCallbackAdapter:
    def __init__(self, session: "QwenAudioStreamingSession") -> None:
        self._session = session

    def on_open(self) -> None:
        self._session._post_callback_event("open", None)

    def on_event(self, result: Any) -> None:
        try:
            sentence = result.get_sentence()
        except Exception:
            sentence = None
        if isinstance(sentence, dict):
            self._session._post_callback_event("sentence", dict(sentence))

    def on_complete(self) -> None:
        self._session._post_callback_event("complete", None)

    def on_error(self, result: Any) -> None:
        self._session._post_callback_event("error", _error_message(result))

    def on_close(self) -> None:
        self._session._post_callback_event("close", None)


class QwenAudioStreamingSession(StreamingTranscriptionSession):
    def __init__(
        self,
        task_id: str,
        time_start: float,
        *,
        recognition_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.task_id = task_id
        self.time_start = time_start
        self.record_stop = time_start
        self.t_submit = 0.0
        self.t_complete = 0.0
        self._recognition_factory = recognition_factory
        self._recognition: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._callback_queue: asyncio.Queue[Any] | None = None
        self._callback_pump: asyncio.Task[None] | None = None
        self._started = False
        self._closed = False
        self._error: str | None = None
        self._sample_rate = get_realtime_sample_rate()
        self._chunk_bytes = max(
            2,
            int(self._sample_rate * 2 * get_realtime_chunk_ms() / 1000),
        )
        self._audio_buffer = bytearray()
        self._audio_bytes_sent = 0
        self._sentences: dict[int, str] = {}
        self._final_sentence_ids: set[int] = set()
        self._fallback_sentence_id = 1
        self._last_full_text = ""
        self._emitted_deltas = False
        self._context_timed_out = False

    def _post_callback_event(self, kind: str, payload: Any) -> None:
        if self._loop is None or self._callback_queue is None or self._closed:
            return
        try:
            self._loop.call_soon_threadsafe(
                self._callback_queue.put_nowait,
                (kind, payload),
            )
        except Exception:
            pass

    async def _capture_request_context(self) -> Any:
        if not should_use_asr_context():
            return None
        from src.polish.llm_polish import prefetch_request_context

        task = asyncio.create_task(
            prefetch_request_context(),
            name=f"qwen_stream_context:{self.task_id}",
        )
        timeout = get_asr_context_capture_timeout_seconds()
        try:
            if timeout <= 0:
                if not task.done():
                    self._context_timed_out = True
                    task.add_done_callback(
                        lambda done: done.exception() if not done.cancelled() else None
                    )
                    return None
                return task.result()
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            self._context_timed_out = True
            task.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            return None
        except asyncio.CancelledError:
            task.cancel()
            raise
        except Exception:
            return None

    def _create_recognition(
        self,
        callback: _RecognitionCallbackAdapter,
        options: dict[str, Any],
        api_key: str,
    ) -> Any:
        factory: Callable[..., Any] | None = self._recognition_factory
        if factory is None:
            from dashscope.audio.asr import Recognition

            factory = cast(Callable[..., Any], Recognition)
        recognition_kwargs: dict[str, Any] = {
            "model": get_realtime_model(),
            "callback": callback,
            "format": get_realtime_format(),
            "sample_rate": self._sample_rate,
            "api_key": api_key,
            **options,
        }
        workspace = get_workspace_id()
        if workspace:
            recognition_kwargs["workspace"] = workspace
        return factory(
            **recognition_kwargs,
        )

    async def start(self) -> None:
        if self._started:
            return
        if get_realtime_format() != "pcm":
            raise ValueError("Qwen Audio 3 live microphone streaming requires realtime_format=pcm")
        api_key = get_api_key()
        if not api_key:
            raise RuntimeError("Qwen Audio 3.0 API key is missing. Set DASHSCOPE_API_KEY.")

        self._loop = asyncio.get_running_loop()
        self._callback_queue = asyncio.Queue()
        self._callback_pump = asyncio.create_task(
            self._pump_callbacks(),
            name=f"qwen_stream_callbacks:{self.task_id}",
        )
        request_context = await self._capture_request_context()
        context_messages = build_asr_context_messages(request_context)
        raw_input = {"context": context_messages} if context_messages else None
        options = build_recognition_options()
        realtime_url = get_realtime_url()

        import dashscope

        dashscope.base_websocket_api_url = realtime_url

        debug_payload = {
            "transport": "dashscope.audio.asr.Recognition",
            "base_websocket_api_url": realtime_url,
            "model": get_realtime_model(),
            "format": get_realtime_format(),
            "sample_rate": self._sample_rate,
            "workspace": get_workspace_id(),
            "parameters": options,
            "input": raw_input or {},
        }
        if should_log_request_payload():
            console.print(
                "[qwen-audio][sdk-stream-config; audio omitted]\n"
                + json.dumps(
                    request_body_for_debug(debug_payload),
                    ensure_ascii=False,
                    indent=2,
                ),
                style="bright_black",
            )

        callback = _RecognitionCallbackAdapter(self)
        self._recognition = self._create_recognition(callback, options, api_key)
        self.t_submit = time.time()
        start_kwargs = {"raw_input": raw_input} if raw_input else {}
        try:
            await asyncio.to_thread(self._recognition.start, **start_kwargs)
        except Exception:
            await self._stop_callback_pump()
            raise
        self._started = True

    async def send_audio(self, audio_chunk: np.ndarray) -> None:
        if self._error:
            raise RuntimeError(self._error)
        if not self._started:
            await self.start()
        pcm = downmix_resample_to_pcm16(audio_chunk, self._sample_rate)
        if not pcm:
            return
        self._audio_buffer.extend(pcm)
        while len(self._audio_buffer) >= self._chunk_bytes:
            frame = bytes(self._audio_buffer[: self._chunk_bytes])
            del self._audio_buffer[: self._chunk_bytes]
            self._send_frame(frame)

    def _send_frame(self, frame: bytes) -> None:
        if not frame or self._recognition is None:
            return
        self._recognition.send_audio_frame(frame)
        self._audio_bytes_sent += len(frame)

    async def finish(self) -> Tuple[str, int, float, float, Dict[str, Any]]:
        self.record_stop = time.time()
        if not self._started or self._recognition is None:
            now = time.time()
            return "", 204, now, now, self._metadata(204)

        if self._audio_buffer:
            self._send_frame(bytes(self._audio_buffer))
            self._audio_buffer.clear()

        stop_task = asyncio.create_task(
            asyncio.to_thread(self._recognition.stop),
            name=f"qwen_stream_stop:{self.task_id}",
        )
        try:
            await asyncio.wait_for(
                asyncio.shield(stop_task),
                timeout=get_realtime_timeout_seconds(),
            )
        except asyncio.TimeoutError:
            self._error = (
                "Qwen Audio 3 realtime SDK stop timed out after "
                f"{get_realtime_timeout_seconds():.1f}s"
            )
            stop_task.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None
            )
        except Exception as exc:
            self._error = f"{exc.__class__.__name__}: {exc}"

        await asyncio.sleep(0)
        await self._stop_callback_pump()
        text = self._current_full_text()
        self.t_complete = time.time()
        status = 200 if text else (502 if self._error else 204)
        self._closed = True
        return text, status, self.t_submit, self.t_complete, self._metadata(status)

    async def cancel(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._started and self._recognition is not None:
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self._recognition.stop),
                    timeout=min(2.0, get_realtime_timeout_seconds()),
                )
            except Exception:
                pass
        await self._stop_callback_pump()

    async def _stop_callback_pump(self) -> None:
        if self._callback_queue is not None and self._callback_pump is not None:
            await self._callback_queue.put(_STOP_EVENT)
            try:
                await self._callback_pump
            except asyncio.CancelledError:
                pass
        self._callback_pump = None

    async def _pump_callbacks(self) -> None:
        assert self._callback_queue is not None
        while True:
            item = await self._callback_queue.get()
            if item is _STOP_EVENT:
                return
            kind, payload = item
            if kind == "sentence":
                await self._handle_sentence(payload)
            elif kind == "error":
                self._error = str(payload or "Qwen Audio 3 realtime SDK error")

    async def _handle_sentence(self, sentence: dict[str, Any]) -> None:
        if bool(sentence.get("heartbeat")):
            return
        text = str(sentence.get("text") or "").strip()
        if not text:
            return
        raw_id = sentence.get("sentence_id")
        try:
            if raw_id is None:
                raise ValueError("missing sentence_id")
            sentence_id = int(raw_id)
        except Exception:
            sentence_id = self._fallback_sentence_id
        if sentence_id in self._final_sentence_ids:
            return
        self._sentences[sentence_id] = text
        if sentence.get("end_time") is not None or bool(sentence.get("sentence_end")):
            self._final_sentence_ids.add(sentence_id)
            self._fallback_sentence_id = max(self._fallback_sentence_id, sentence_id + 1)

        full_text = self._current_full_text()
        if full_text == self._last_full_text:
            return
        self._last_full_text = full_text
        if should_emit_realtime_deltas():
            await Cosmic.queue_out.put(
                {
                    "task_id": self.task_id,
                    "is_final": False,
                    "is_transcript_delta": True,
                    "text": full_text,
                    "time_start": self.time_start,
                    "time_stop": self.record_stop,
                    "time_submit": self.t_submit or time.time(),
                    "time_complete": time.time(),
                    "source": "mic",
                    "stream": True,
                    "transcript_revision_mode": "full_text",
                }
            )
            self._emitted_deltas = True

    def _current_full_text(self) -> str:
        return join_transcript_segments(
            [self._sentences[key] for key in sorted(self._sentences)]
        )

    def _metadata(self, status: int) -> Dict[str, Any]:
        request_id = None
        first_package_delay = None
        last_package_delay = None
        if self._recognition is not None:
            try:
                request_id = self._recognition.get_last_request_id()
            except Exception:
                pass
            try:
                first_package_delay = self._recognition.get_first_package_delay()
            except Exception:
                pass
            try:
                last_package_delay = self._recognition.get_last_package_delay()
            except Exception:
                pass
        return {
            "streaming": True,
            "provider": "qwen-audio",
            "via": "qwen-audio-3-sdk",
            "model": get_realtime_model(),
            "sample_rate": self._sample_rate,
            "audio_bytes_sent": self._audio_bytes_sent,
            "emitted_deltas": self._emitted_deltas,
            "asr_context_capture_timed_out": self._context_timed_out,
            "request_id": request_id,
            "first_package_delay_ms": first_package_delay,
            "last_package_delay_ms": last_package_delay,
            "error": self._error,
            "status": status,
        }
