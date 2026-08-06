from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections.abc import Mapping
from typing import Any, Dict, Tuple
from urllib.parse import urlencode

import numpy as np
import websockets

from src.infra.cosmic import Cosmic
from src.provider.provider_settings import (
    get_bool as ps_get_bool,
    get_int as ps_get_int,
    get_str as ps_get_str,
)
from src.transcribe.streaming import StreamingTranscriptionSession

_DEFAULT_HOST = "asr.cloud.tencent.com"
_DEFAULT_ENGINE = "Hy-ASR-3.0-preview"
_SAMPLE_RATE = 16000
_MAX_AUDIO_SECONDS = 60


class TencentProtocolError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"Tencent ASR error {code}: {message}")
        self.code = code


def get_app_id() -> str:
    return ps_get_str("app_id", env="TENCENT_ASR_APP_ID", default="") or ""


def get_secret_id() -> str:
    return ps_get_str("secret_id", env="TENCENT_ASR_SECRET_ID", default="") or ""


def get_secret_key() -> str:
    return ps_get_str("secret_key", env="TENCENT_ASR_SECRET_KEY", default="") or ""


def get_host() -> str:
    return (
        (
            ps_get_str("realtime_host", env="TENCENT_ASR_HOST", default=_DEFAULT_HOST)
            or _DEFAULT_HOST
        )
        .strip()
        .rstrip("/")
    )


def get_engine_model_type() -> str:
    return (
        ps_get_str(
            "model", env="TENCENT_ASR_ENGINE_MODEL_TYPE", default=_DEFAULT_ENGINE
        )
        or _DEFAULT_ENGINE
    )


def get_chunk_ms() -> int:
    value = ps_get_int("chunk_ms", env="TENCENT_ASR_CHUNK_MS", default=200)
    return max(40, min(200, int(value or 200)))


def get_timeout_seconds() -> float:
    raw = ps_get_str("timeout_seconds", env="TENCENT_ASR_TIMEOUT_SECONDS", default="30")
    try:
        return max(1.0, min(120.0, float(raw or "30")))
    except ValueError:
        return 30.0


def should_emit_deltas() -> bool:
    return ps_get_bool(
        "realtime_emit_deltas",
        env="TENCENT_ASR_REALTIME_EMIT_DELTAS",
        default=True,
    )


def build_query_params(
    voice_id: str,
    *,
    timestamp: int | None = None,
    nonce: int | None = None,
) -> dict[str, str | int]:
    now = int(time.time()) if timestamp is None else timestamp
    params: dict[str, str | int] = {
        "engine_model_type": get_engine_model_type(),
        "expired": now + 86400,
        "filter_dirty": ps_get_int("filter_dirty", default=0) or 0,
        "filter_modal": ps_get_int("filter_modal", default=0) or 0,
        "filter_punc": ps_get_int("filter_punc", default=0) or 0,
        "needvad": 0,
        "nonce": nonce if nonce is not None else secrets.randbelow(9_999_999) + 1,
        "secretid": get_secret_id(),
        "timestamp": now,
        "voice_format": 1,
        "voice_id": voice_id,
    }
    convert_num_mode = ps_get_int("convert_num_mode", default=1)
    if convert_num_mode is not None:
        params["convert_num_mode"] = convert_num_mode
    return params


def sign_query(app_id: str, params: Mapping[str, str | int], secret_key: str) -> str:
    query = urlencode(sorted(params.items()))
    source = f"{get_host()}/asr/v2/{app_id}?{query}"
    digest = hmac.new(
        secret_key.encode("utf-8"), source.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def build_websocket_url(
    voice_id: str,
    *,
    timestamp: int | None = None,
    nonce: int | None = None,
) -> str:
    app_id = get_app_id()
    secret_id = get_secret_id()
    secret_key = get_secret_key()
    if not app_id or not secret_id or not secret_key:
        raise RuntimeError(
            "Tencent ASR credentials are missing. Configure app_id, secret_id, "
            "and secret_key, or set TENCENT_ASR_APP_ID, "
            "TENCENT_ASR_SECRET_ID, and TENCENT_ASR_SECRET_KEY."
        )
    params = build_query_params(voice_id, timestamp=timestamp, nonce=nonce)
    params["signature"] = sign_query(app_id, params, secret_key)
    return f"wss://{get_host()}/asr/v2/{app_id}?{urlencode(sorted(params.items()))}"


def downmix_resample_to_pcm16(audio_chunk: np.ndarray) -> bytes:
    if audio_chunk.size == 0:
        return b""
    mono = audio_chunk.mean(axis=1) if audio_chunk.ndim == 2 else audio_chunk
    mono = np.nan_to_num(mono, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
    mono = np.clip(mono, -1.0, 1.0)
    mono = mono[::3]
    return (mono * 32767.0).astype("<i2", copy=False).tobytes()


def parse_message(message: str | bytes) -> dict[str, Any]:
    if isinstance(message, bytes):
        message = message.decode("utf-8")
    payload = json.loads(message)
    if not isinstance(payload, dict):
        raise TencentProtocolError(0, "response is not a JSON object")
    code = int(payload.get("code") or 0)
    if code != 0:
        raise TencentProtocolError(code, str(payload.get("message") or "unknown error"))
    return payload


def join_segments(segments: Mapping[int, str]) -> str:
    result = ""
    for index in sorted(segments):
        segment = str(segments[index] or "").strip()
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


class TencentStreamingSession(StreamingTranscriptionSession):
    def __init__(self, task_id: str, time_start: float) -> None:
        self.task_id = task_id
        self.time_start = time_start
        self.record_stop = time_start
        self.t_submit = 0.0
        self.t_complete = 0.0
        self._voice_id = str(uuid.uuid4())
        self._connection: Any = None
        self._websocket: Any = None
        self._receiver: asyncio.Task[str] | None = None
        self._audio_buffer = bytearray()
        self._chunk_bytes = _SAMPLE_RATE * 2 * get_chunk_ms() // 1000
        self._audio_bytes_sent = 0
        self._segments: dict[int, str] = {}
        self._last_text = ""
        self._emitted_deltas = False
        self._closed = False

    async def start(self) -> None:
        if self._websocket is not None:
            return
        self._connection = websockets.connect(
            build_websocket_url(self._voice_id),
            open_timeout=get_timeout_seconds(),
            close_timeout=5,
            max_size=8 * 1024 * 1024,
        )
        self._websocket = await self._connection
        self.t_submit = time.time()
        try:
            handshake = await asyncio.wait_for(
                self._websocket.recv(), timeout=get_timeout_seconds()
            )
            parse_message(handshake)
        except BaseException:
            await self._close()
            raise
        self._receiver = asyncio.create_task(
            self._receive_responses(), name=f"tencent_stream_recv:{self.task_id}"
        )

    async def send_audio(self, audio_chunk: np.ndarray) -> None:
        if self._websocket is None:
            await self.start()
        pcm = downmix_resample_to_pcm16(audio_chunk)
        if not pcm:
            return
        if self._audio_bytes_sent + len(self._audio_buffer) + len(pcm) > (
            _SAMPLE_RATE * 2 * _MAX_AUDIO_SECONDS
        ):
            raise ValueError(
                "Tencent Hy-ASR-3.0-preview currently supports at most 60 seconds "
                "of audio per recognition session"
            )
        self._audio_buffer.extend(pcm)
        while len(self._audio_buffer) >= self._chunk_bytes:
            frame = bytes(self._audio_buffer[: self._chunk_bytes])
            del self._audio_buffer[: self._chunk_bytes]
            await self._send_frame(frame)

    async def _send_frame(self, frame: bytes) -> None:
        if self._websocket is None or not frame:
            return
        await self._websocket.send(frame)
        self._audio_bytes_sent += len(frame)

    async def _receive_responses(self) -> str:
        if self._websocket is None:
            return ""
        while True:
            payload = parse_message(await self._websocket.recv())
            result = payload.get("result")
            if isinstance(result, dict):
                index = int(result.get("index") or 0)
                text = str(result.get("voice_text_str") or "").strip()
                if text:
                    self._segments[index] = text
                    full_text = join_segments(self._segments)
                    if full_text and full_text != self._last_text:
                        self._last_text = full_text
                        await self._emit_revision(full_text)
            if int(payload.get("final") or 0) == 1:
                return self._last_text

    async def _emit_revision(self, text: str) -> None:
        if not should_emit_deltas():
            return
        await Cosmic.queue_out.put(
            {
                "task_id": self.task_id,
                "is_final": False,
                "is_transcript_delta": True,
                "text": text,
                "time_start": self.time_start,
                "time_stop": self.record_stop,
                "time_submit": self.t_submit,
                "time_complete": time.time(),
                "source": "mic",
                "stream": True,
                "transcript_revision_mode": "full_text",
            }
        )
        self._emitted_deltas = True

    async def finish(self) -> Tuple[str, int, float, float, Dict[str, Any]]:
        self.record_stop = time.time()
        if self._websocket is None or self._receiver is None:
            now = time.time()
            return "", 204, now, now, self._metadata(error=None)
        if self._audio_buffer:
            await self._send_frame(bytes(self._audio_buffer))
            self._audio_buffer.clear()
        error: str | None = None
        try:
            await self._websocket.send(json.dumps({"type": "end"}))
            text = await asyncio.wait_for(self._receiver, timeout=get_timeout_seconds())
            status = 200 if text else 204
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            text = ""
            status = 502
            error = f"{exc.__class__.__name__}: {exc}"
        self.t_complete = time.time()
        await self._close()
        return text, status, self.t_submit, self.t_complete, self._metadata(error=error)

    async def cancel(self) -> None:
        await self._close()

    async def _close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._receiver is not None and not self._receiver.done():
            self._receiver.cancel()
            await asyncio.gather(self._receiver, return_exceptions=True)
        if self._websocket is not None:
            try:
                await self._websocket.close()
            except Exception:
                pass

    def _metadata(self, *, error: str | None) -> Dict[str, Any]:
        return {
            "provider": "tencent",
            "via": "tencent-realtime-asr-websocket",
            "voice_id": self._voice_id,
            "engine_model_type": get_engine_model_type(),
            "sample_rate": _SAMPLE_RATE,
            "audio_bytes_sent": self._audio_bytes_sent,
            "streaming": True,
            "emitted_deltas": self._emitted_deltas,
            "error": error,
        }
