"""Qwen Audio Legacy realtime integration via the official SDK."""
from __future__ import annotations

import asyncio
import base64
import json
import math
import time
from typing import Any, Callable, Dict, Tuple, cast

import numpy as np

from src.infra.cosmic import Cosmic, console
from src.transcribe.qwen_audio_legacy import settings
from src.transcribe.streaming import StreamingTranscriptionSession


def _rt_log(message: str, style: str = "bright_black") -> None:
    if not settings.should_show_realtime_logs():
        return
    try:
        console.print(f"[qwen-audio-legacy][realtime] {message}", style=style)
    except Exception:
        pass


def _event_type(message: Dict[str, Any]) -> str:
    value = message.get("type")
    if isinstance(value, str):
        return value
    header = message.get("header")
    if isinstance(header, dict):
        event = header.get("event")
        if isinstance(event, str):
            return event
    return ""


def _extract_text_fragment(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return "".join(_extract_text_fragment(item) for item in message)
    if not isinstance(message, dict):
        return ""

    text_value = message.get("text")
    stash_value = message.get("stash")
    if isinstance(text_value, str) or isinstance(stash_value, str):
        combined = f"{text_value if isinstance(text_value, str) else ''}{stash_value if isinstance(stash_value, str) else ''}"
        if combined:
            return combined

    for key in ("transcript", "text", "delta", "result"):
        value = message.get(key)
        if isinstance(value, str) and value:
            return value

    for key in ("item", "content", "output", "payload", "response", "session"):
        value = message.get(key)
        text = _extract_text_fragment(value)
        if text:
            return text
    return ""


def _message_preview(message: Dict[str, Any], max_chars: int = 700) -> str:
    def scrub(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(k): scrub(v)
                for k, v in value.items()
                if str(k).lower() not in {"audio", "input_audio", "authorization"}
            }
        if isinstance(value, list):
            return [scrub(item) for item in value[:6]]
        if isinstance(value, str):
            if len(value) > 160:
                return value[:160] + f"...<{len(value)} chars>"
            return value
        return value

    try:
        text = json.dumps(scrub(message), ensure_ascii=False)
    except Exception:
        text = str(message)
    if len(text) > max_chars:
        return text[:max_chars] + f"...<{len(text)} chars>"
    return text


def _text_field_lengths(message: Dict[str, Any]) -> Dict[str, int]:
    lengths: Dict[str, int] = {}

    def walk(value: Any, path: str) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                child_path = f"{path}.{key}" if path else str(key)
                if key in {"text", "stash", "transcript", "delta", "result"} and isinstance(child, str):
                    lengths[child_path] = len(child)
                walk(child, child_path)
        elif isinstance(value, list):
            for i, child in enumerate(value[:8]):
                walk(child, f"{path}[{i}]")

    walk(message, "")
    return lengths


def _downmix_resample_to_pcm16(audio_chunk: np.ndarray, target_sr: int) -> bytes:
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
            step = 48000 // target_sr
            mono = mono[::step]
        else:
            src_pos = np.arange(len(mono), dtype=np.float64)
            dst_len = max(1, int(round(len(mono) * target_sr / 48000)))
            dst_pos = np.linspace(0, max(0, len(mono) - 1), dst_len)
            mono = np.interp(dst_pos, src_pos, mono).astype(np.float32, copy=False)

    pcm = (mono * 32767.0).astype("<i2", copy=False)
    return pcm.tobytes()


def build_realtime_url() -> str:
    """Return the SDK base URL; OmniRealtimeConversation adds the model query."""
    return settings.get_realtime_url().split("?", 1)[0].rstrip("/")


_STOP_EVENT = object()


def _join_segments(segments: list[str]) -> str:
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


class _OmniCallbackAdapter:
    def __init__(self, session: "QwenAudioLegacyRealtimeSession") -> None:
        self._session = session

    def on_open(self) -> None:
        self._session._post_callback_event("open", None)

    def on_event(self, response: Any) -> None:
        if isinstance(response, dict):
            self._session._post_callback_event("event", dict(response))

    def on_close(self, close_status_code: Any, close_msg: Any) -> None:
        self._session._post_callback_event(
            "close",
            {"code": close_status_code, "message": close_msg},
        )


class QwenAudioLegacyRealtimeSession(StreamingTranscriptionSession):
    def __init__(
        self,
        task_id: str,
        time_start: float,
        record_stop: float | None = None,
        *,
        conversation_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.task_id = task_id
        self.time_start = time_start
        self.record_stop = record_stop or time_start
        self.t_submit = 0.0
        self.t_complete = 0.0
        self.status_code = 0
        self._conversation_factory = conversation_factory
        self._conversation: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._callback_queue: asyncio.Queue[Any] | None = None
        self._callback_pump: asyncio.Task[None] | None = None
        self._session_ready = asyncio.Event()
        self._started = False
        self._closed = False
        self._partial_text = ""
        self._final_segments: list[str] = []
        self._last_emitted_text = ""
        self._error: str | None = None
        self._sample_rate = settings.get_realtime_sample_rate()
        self._audio_bytes_sent = 0
        self._emitted_deltas = False
        self._event_types: list[str] = []
        self._last_messages: list[Dict[str, Any]] = []

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

    def _create_conversation(self, callback: _OmniCallbackAdapter) -> Any:
        factory = self._conversation_factory
        if factory is None:
            from dashscope.audio.qwen_omni import OmniRealtimeConversation

            factory = cast(Callable[..., Any], OmniRealtimeConversation)
        kwargs: dict[str, Any] = {
            "model": settings.get_realtime_model(),
            "url": build_realtime_url(),
            "callback": callback,
            "api_key": settings.get_api_key(),
        }
        workspace = settings.get_workspace_id()
        if workspace:
            kwargs["workspace"] = workspace
        return factory(**kwargs)

    @staticmethod
    def _build_transcription_params() -> Any:
        from dashscope.audio.qwen_omni.omni_realtime import TranscriptionParams

        kwargs: dict[str, Any] = {
            "sample_rate": settings.get_realtime_sample_rate(),
            "input_audio_format": settings.get_realtime_format(),
        }
        language = settings.get_language()
        if language:
            kwargs["language"] = language
        context = settings.get_context_text()
        if context:
            kwargs["corpus_text"] = context
        return TranscriptionParams(**kwargs)

    async def start(self) -> None:
        if self._started:
            return
        if settings.get_realtime_format() != "pcm":
            raise ValueError(
                "qwen-audio-legacy microphone realtime streaming requires "
                "realtime_format=pcm"
            )
        self._loop = asyncio.get_running_loop()
        self._callback_queue = asyncio.Queue()
        self._callback_pump = asyncio.create_task(
            self._pump_callbacks(),
            name=f"qwen_audio_legacy_sdk_callbacks:{self.task_id}",
        )
        self.t_submit = time.time()
        url = build_realtime_url()
        _rt_log(
            f"connect model={settings.get_realtime_model()} "
            f"sample_rate={self._sample_rate} format={settings.get_realtime_format()} "
            f"vad={settings.get_realtime_enable_vad()} url={url}"
        )
        callback = _OmniCallbackAdapter(self)
        self._conversation = self._create_conversation(callback)
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._conversation.connect),
                timeout=min(10.0, settings.get_realtime_timeout_seconds()),
            )
            from dashscope.audio.qwen_omni import MultiModality

            update_kwargs = {
                "output_modalities": [MultiModality.TEXT],
                "enable_input_audio_transcription": True,
                "enable_turn_detection": settings.get_realtime_enable_vad(),
                "turn_detection_type": "server_vad",
                "turn_detection_threshold": settings.get_realtime_vad_threshold(),
                "turn_detection_silence_duration_ms": (
                    settings.get_realtime_vad_silence_ms()
                ),
                "transcription_params": self._build_transcription_params(),
            }
            await asyncio.to_thread(
                self._conversation.update_session,
                **update_kwargs,
            )
            await asyncio.wait_for(
                self._session_ready.wait(),
                timeout=min(10.0, settings.get_realtime_timeout_seconds()),
            )
            if self._error:
                raise RuntimeError(self._error)
        except Exception:
            self._closed = True
            await self._close_conversation()
            await self._stop_callback_pump()
            raise
        self._started = True
        _rt_log("session.update acknowledged")

    async def send_audio(self, audio_chunk: np.ndarray) -> None:
        if self._closed:
            return
        if self._error:
            raise RuntimeError(self._error)
        if not self._started:
            await self.start()
        pcm = _downmix_resample_to_pcm16(audio_chunk, self._sample_rate)
        if not pcm:
            return
        self._audio_bytes_sent += len(pcm)
        if self._audio_bytes_sent == len(pcm) or self._audio_bytes_sent % (self._sample_rate * 2) < len(pcm):
            _rt_log(f"append audio chunk_bytes={len(pcm)} total_bytes={self._audio_bytes_sent}")
        await asyncio.to_thread(
            self._conversation.append_audio,
            base64.b64encode(pcm).decode("ascii"),
        )

    async def finish(self) -> Tuple[str, int, float, float, Dict[str, Any]]:
        self.record_stop = time.time()
        if not self._started or self._conversation is None:
            self.t_submit = time.time()
            self.t_complete = self.t_submit
            return "", 204, self.t_submit, self.t_complete, self._metadata()

        t_finish_submit = time.time()
        timeout = settings.get_realtime_timeout_seconds()
        try:
            if not settings.get_realtime_enable_vad():
                _rt_log("SDK commit")
                await asyncio.to_thread(self._conversation.commit)
            _rt_log("SDK end_session")
            await asyncio.wait_for(
                asyncio.to_thread(
                    self._conversation.end_session,
                    max(1, int(math.ceil(timeout))),
                ),
                timeout=timeout + 1.0,
            )
        except asyncio.TimeoutError:
            self._error = (
                "qwen-audio-legacy realtime finish timed out after "
                f"{timeout:.1f}s"
            )
            console.print(self._error, style="bright_yellow")
        except Exception as exc:
            if not self._error:
                self._error = f"{exc.__class__.__name__}: {exc}"

        self._closed = True
        await self._close_conversation()
        await asyncio.sleep(0)
        await self._stop_callback_pump()
        text = self._current_full_text()
        self.t_complete = time.time()
        self.t_submit = t_finish_submit
        self.status_code = 200 if text else (502 if self._error else 204)
        meta = self._metadata()
        if not text and settings.should_show_debug_logs():
            console.print(
                "qwen-audio-legacy realtime returned empty transcript; "
                f"audio_bytes_sent={self._audio_bytes_sent} events={self._event_types}",
                style="bright_yellow",
            )
        _rt_log(
            f"finish text_len={len(text)} status={self.status_code} "
            f"audio_bytes_sent={self._audio_bytes_sent} events={self._event_types} error={self._error}",
            style="bright_yellow" if not text else "bright_black",
        )
        return text, self.status_code, self.t_submit, self.t_complete, meta

    async def cancel(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._close_conversation()
        await self._stop_callback_pump()

    async def _pump_callbacks(self) -> None:
        assert self._callback_queue is not None
        while True:
            item = await self._callback_queue.get()
            if item is _STOP_EVENT:
                return
            kind, payload = item
            if kind == "event":
                await self._handle_event(payload)
            elif kind == "close" and not self._closed:
                self._error = (
                    "qwen-audio-legacy realtime SDK connection closed unexpectedly: "
                    f"{payload}"
                )
                self._session_ready.set()

    async def _stop_callback_pump(self) -> None:
        if self._callback_queue is not None and self._callback_pump is not None:
            await self._callback_queue.put(_STOP_EVENT)
            try:
                await self._callback_pump
            except asyncio.CancelledError:
                pass
        self._callback_pump = None

    async def _handle_event(self, message: Dict[str, Any]) -> None:
        event = _event_type(message)
        event_lower = event.lower()
        self._remember_event(event, message)
        text = _extract_text_fragment(message)
        _rt_log(
            f"recv event={event or '<missing>'} text_len={len(text)} "
            f"fields={_text_field_lengths(message)} "
            f"preview={_message_preview(message)}"
        )
        if event_lower in {"session.created", "session.updated"}:
            if event_lower == "session.updated":
                self._session_ready.set()
            return
        if event_lower == "error" or event_lower.endswith(".failed"):
            self._error = json.dumps(message, ensure_ascii=False)
            self._session_ready.set()
            return
        if not text:
            return

        if event_lower.endswith(".completed") or event_lower.endswith(".done"):
            if not self._final_segments or text != self._final_segments[-1]:
                self._final_segments.append(text)
            self._partial_text = ""
        else:
            self._partial_text = text
        full_text = self._current_full_text()
        if full_text == self._last_emitted_text:
            return
        self._last_emitted_text = full_text
        if settings.should_emit_realtime_deltas():
            await self._emit_delta(full_text)
            self._emitted_deltas = True

    def _remember_event(self, event: str, message: Dict[str, Any]) -> None:
        if event:
            self._event_types.append(event)
        slim = {
            key: value
            for key, value in message.items()
            if str(key).lower() not in {"audio", "input_audio", "authorization"}
        }
        self._last_messages.append(slim)
        if len(self._last_messages) > 8:
            self._last_messages = self._last_messages[-8:]
        if len(self._event_types) > 80:
            self._event_types = self._event_types[-80:]

    async def _emit_delta(self, text: str) -> None:
        if not text:
            return
        await Cosmic.queue_out.put(
            {
                "task_id": self.task_id,
                "is_final": False,
                "is_transcript_delta": True,
                "text": text,
                "time_start": self.time_start,
                "time_stop": self.record_stop,
                "time_submit": self.t_submit or time.time(),
                "time_complete": time.time(),
                "source": "mic",
                "stream": True,
                # Qwen-ASR Realtime returns a finalized prefix in `text` and a
                # revisable tail in `stash`. `_extract_text_fragment` combines
                # them into the current full hypothesis, not an append-only
                # character delta.
                "transcript_revision_mode": "full_text",
            }
        )

    def _current_full_text(self) -> str:
        segments = list(self._final_segments)
        if self._partial_text:
            segments.append(self._partial_text)
        return _join_segments(segments)

    async def _close_conversation(self) -> None:
        if self._conversation is None:
            return
        try:
            await asyncio.wait_for(
                asyncio.to_thread(self._conversation.close),
                timeout=settings.get_realtime_close_timeout_seconds(),
            )
        except Exception:
            pass

    def _metadata(self) -> Dict[str, Any]:
        session_id = None
        response_id = None
        first_text_delay = None
        if self._conversation is not None:
            for attr, method_name in (
                ("session_id", "get_session_id"),
                ("response_id", "get_last_response_id"),
                ("first_text_delay", "get_last_first_text_delay"),
            ):
                try:
                    value = getattr(self._conversation, method_name)()
                except Exception:
                    value = None
                if attr == "session_id":
                    session_id = value
                elif attr == "response_id":
                    response_id = value
                else:
                    first_text_delay = value
        return {
            "streaming": True,
            "provider": "qwen-audio-legacy",
            "via": "qwen-audio-legacy-sdk-omni-realtime",
            "model": settings.get_realtime_model(),
            "sample_rate": self._sample_rate,
            "error": self._error,
            "audio_bytes_sent": self._audio_bytes_sent,
            "emitted_deltas": self._emitted_deltas,
            "event_types": list(self._event_types),
            "last_messages": list(self._last_messages),
            "session_id": session_id,
            "response_id": response_id,
            "first_text_delay_ms": first_text_delay,
        }
