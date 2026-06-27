"""DashScope Qwen-ASR-Realtime WebSocket integration."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import time
import uuid
from typing import Any, Dict, Tuple
from urllib.parse import urlencode

import numpy as np
import websockets

from src.infra.cosmic import Cosmic, console
from src.transcribe.dashscope import settings
from src.transcribe.streaming import StreamingTranscriptionSession


def _rt_log(message: str, style: str = "bright_black") -> None:
    if not settings.should_show_realtime_logs():
        return
    try:
        console.print(f"[dashscope-realtime] {message}", style=style)
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
            return {str(k): scrub(v) for k, v in value.items()}
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


def _is_done_event(event: str) -> bool:
    event = event.lower()
    return event in {
        "input_audio_buffer.committed",
        "conversation.item.input_audio_transcription.completed",
        "conversation.item.input_audio_transcription.failed",
        "session.finished",
        "session.closed",
        "error",
    } or event.endswith(".done") or event.endswith(".completed") or event.endswith(".failed")


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
    base = settings.get_realtime_url()
    separator = "&" if "?" in base else "?"
    return f"{base}{separator}{urlencode({'model': settings.get_realtime_model()})}"


class DashScopeRealtimeSession(StreamingTranscriptionSession):
    def __init__(self, task_id: str, time_start: float, record_stop: float | None = None) -> None:
        self.task_id = task_id
        self.time_start = time_start
        self.record_stop = record_stop or time_start
        self.t_submit = 0.0
        self.t_complete = 0.0
        self.status_code = 0
        self._ws = None
        self._reader_task: asyncio.Task[None] | None = None
        self._session_ready = asyncio.Event()
        self._done = asyncio.Event()
        self._started = False
        self._closed = False
        self._last_text = ""
        self._final_text = ""
        self._error: str | None = None
        self._sample_rate = settings.get_realtime_sample_rate()
        self._audio_bytes_sent = 0
        self._emitted_deltas = False
        self._event_types: list[str] = []
        self._last_messages: list[Dict[str, Any]] = []

    def _event_id(self, prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex}"

    async def start(self) -> None:
        if self._started:
            return
        self.t_submit = time.time()
        url = build_realtime_url()
        _rt_log(
            f"connect model={settings.get_realtime_model()} "
            f"sample_rate={self._sample_rate} format={settings.get_realtime_format()} "
            f"vad={settings.get_realtime_enable_vad()} url={url}"
        )
        headers = {
            "Authorization": f"Bearer {settings.get_api_key()}",
            "OpenAI-Beta": "realtime=v1",
        }
        self._ws = await websockets.connect(
            url,
            extra_headers=headers,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=8 * 1024 * 1024,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        session_update = settings.build_realtime_session_update()
        _rt_log(f"send session.update preview={_message_preview(session_update)}")
        await self._send_json(session_update)
        try:
            await asyncio.wait_for(self._session_ready.wait(), timeout=min(10.0, settings.get_realtime_timeout_seconds()))
        except asyncio.TimeoutError as exc:
            raise TimeoutError("DashScope realtime session.update was not acknowledged") from exc
        self._started = True
        _rt_log("session.update acknowledged")

    async def send_audio(self, audio_chunk: np.ndarray) -> None:
        if self._closed:
            return
        if not self._started:
            await self.start()
        pcm = _downmix_resample_to_pcm16(audio_chunk, self._sample_rate)
        if not pcm:
            return
        self._audio_bytes_sent += len(pcm)
        if self._audio_bytes_sent == len(pcm) or self._audio_bytes_sent % (self._sample_rate * 2) < len(pcm):
            _rt_log(f"append audio chunk_bytes={len(pcm)} total_bytes={self._audio_bytes_sent}")
        await self._send_json(
            {
                "event_id": self._event_id("event_append"),
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }
        )

    async def finish(self) -> Tuple[str, int, float, float, Dict[str, Any]]:
        if not self._started:
            self.t_submit = time.time()
            self.t_complete = self.t_submit
            return "", 204, self.t_submit, self.t_complete, {"streaming": True, "provider": "dashscope"}

        if not settings.get_realtime_enable_vad():
            _rt_log("send input_audio_buffer.commit")
            await self._send_json(
                {
                    "event_id": self._event_id("event_commit"),
                    "type": "input_audio_buffer.commit",
                }
            )
        _rt_log("send session.finish")
        await self._send_json(
            {
                "event_id": self._event_id("event_finish"),
                "type": "session.finish",
            }
        )
        timeout = settings.get_realtime_timeout_seconds()
        try:
            await asyncio.wait_for(self._done.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            self._error = f"DashScope realtime finish timed out after {timeout:.1f}s"
            console.print(self._error, style="bright_yellow")
        await self._close()
        text = self._final_text or self._last_text
        self.t_complete = time.time()
        self.status_code = 200 if text else (502 if self._error else 204)
        meta = {
            "streaming": True,
            "provider": "dashscope",
            "model": settings.get_realtime_model(),
            "sample_rate": self._sample_rate,
            "error": self._error,
            "audio_bytes_sent": self._audio_bytes_sent,
            "emitted_deltas": self._emitted_deltas,
            "event_types": list(self._event_types),
            "last_messages": list(self._last_messages),
        }
        if not text and settings.should_show_debug_logs():
            console.print(
                "DashScope realtime returned empty transcript; "
                f"audio_bytes_sent={self._audio_bytes_sent} events={self._event_types}",
                style="bright_yellow",
            )
        _rt_log(
            f"finish text_len={len(text)} status={self.status_code} "
            f"audio_bytes_sent={self._audio_bytes_sent} events={self._event_types} error={self._error}",
            style="bright_yellow" if not text else "bright_black",
        )
        if not text:
            for i, message in enumerate(self._last_messages, 1):
                _rt_log(f"recent_message[{i}]={_message_preview(message)}", style="bright_yellow")
        return text, self.status_code, self.t_submit, self.t_complete, meta

    async def cancel(self) -> None:
        self._closed = True
        await self._close()

    async def _send_json(self, payload: Dict[str, Any]) -> None:
        if self._ws is None:
            return
        await self._ws.send(json.dumps(payload, ensure_ascii=False))

    async def _read_loop(self) -> None:
        try:
            if self._ws is None:
                return
            async for raw in self._ws:
                if isinstance(raw, bytes):
                    continue
                try:
                    message = json.loads(raw)
                except Exception:
                    continue
                event = _event_type(message)
                event_lower = event.lower()
                self._remember_event(event, message)
                text = _extract_text_fragment(message)
                _rt_log(
                    f"recv event={event or '<missing>'} text_len={len(text)} "
                    f"fields={_text_field_lengths(message)} preview={_message_preview(message)}"
                )
                if event_lower == "session.updated":
                    self._session_ready.set()
                if event_lower == "error" or event_lower.endswith(".failed"):
                    self._error = json.dumps(message, ensure_ascii=False)
                    self._session_ready.set()
                    self._done.set()
                    return

                if text:
                    if event_lower.endswith(".completed") or event_lower.endswith(".done"):
                        if text != self._final_text:
                            self._final_text = f"{self._final_text}{text}" if self._final_text else text
                    self._last_text = text
                    if settings.should_emit_realtime_deltas():
                        await self._emit_delta(text)
                        self._emitted_deltas = True

                if event_lower in {"session.finished", "session.closed"}:
                    self._done.set()
                    return
                if _is_done_event(event) and self._final_text:
                    self._done.set()
                    return
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._error = f"{exc.__class__.__name__}: {exc}"
            self._done.set()

    def _remember_event(self, event: str, message: Dict[str, Any]) -> None:
        if event:
            self._event_types.append(event)
        slim = dict(message)
        audio = slim.get("audio")
        if isinstance(audio, str):
            slim["audio"] = f"<base64:{len(audio)} chars>"
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
            }
        )

    async def _close(self) -> None:
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._reader_task
            self._reader_task = None
        if self._ws is not None:
            with contextlib.suppress(Exception):
                await self._ws.close()
            self._ws = None
