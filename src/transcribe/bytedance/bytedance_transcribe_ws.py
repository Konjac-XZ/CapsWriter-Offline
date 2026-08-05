from __future__ import annotations

import asyncio
import gzip
import json
import struct
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, Tuple

import numpy as np
import websockets

from src.infra.cosmic import Cosmic
from src.provider.provider_settings import (
    get_bool as ps_get_bool,
    get_int as ps_get_int,
    get_str as ps_get_str,
)
from src.transcribe.streaming import StreamingTranscriptionSession

_DEFAULT_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
_DEFAULT_RESOURCE_ID = "volc.seedasr.sauc.duration"
_FULL_CLIENT_REQUEST = 0x1
_AUDIO_ONLY_REQUEST = 0x2
_FULL_SERVER_RESPONSE = 0x9
_ERROR_RESPONSE = 0xF
_NO_SEQUENCE = 0x0
_LAST_PACKET = 0x2
_JSON = 0x1
_NO_SERIALIZATION = 0x0
_GZIP = 0x1


class ByteDanceProtocolError(RuntimeError):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"ByteDance ASR error {code}: {message}")
        self.code = code


def get_api_key() -> str:
    return ps_get_str("api_key", env="BYTEDANCE_ASR_API_KEY", default="") or ""


def get_url() -> str:
    return (
        ps_get_str("realtime_url", env="BYTEDANCE_ASR_URL", default=_DEFAULT_URL)
        or _DEFAULT_URL
    ).rstrip("/")


def get_resource_id() -> str:
    explicit = ps_get_str(
        "realtime_resource_id",
        env="BYTEDANCE_ASR_REALTIME_RESOURCE_ID",
        default=None,
    )
    if explicit:
        return explicit
    return (
        ps_get_str(
            "resource_id",
            env="BYTEDANCE_ASR_RESOURCE_ID",
            default=_DEFAULT_RESOURCE_ID,
        )
        or _DEFAULT_RESOURCE_ID
    )


def get_sample_rate() -> int:
    value = ps_get_int("sample_rate", env="BYTEDANCE_ASR_SAMPLE_RATE", default=16000)
    if value != 16000:
        raise ValueError("ByteDance Doubao ASR currently requires sample_rate=16000")
    return 16000


def get_chunk_ms() -> int:
    value = ps_get_int("chunk_ms", env="BYTEDANCE_ASR_CHUNK_MS", default=200)
    return max(100, min(200, int(value or 200)))


def get_timeout_seconds() -> float:
    raw = ps_get_str(
        "timeout_seconds", env="BYTEDANCE_ASR_TIMEOUT_SECONDS", default="30"
    )
    try:
        return max(1.0, min(120.0, float(raw or "30")))
    except ValueError:
        return 30.0


def should_use_realtime() -> bool:
    return ps_get_bool("realtime", env="BYTEDANCE_ASR_REALTIME", default=True)


def should_emit_deltas() -> bool:
    return ps_get_bool(
        "realtime_emit_deltas",
        env="BYTEDANCE_ASR_REALTIME_EMIT_DELTAS",
        default=True,
    )


def build_headers(connect_id: str | None = None) -> dict[str, str]:
    headers = {
        "X-Api-Resource-Id": get_resource_id(),
        "X-Api-Connect-Id": connect_id or str(uuid.uuid4()),
    }
    api_key = get_api_key()
    if api_key:
        headers["X-Api-Key"] = api_key
        return headers

    app_key = ps_get_str("app_key", env="BYTEDANCE_ASR_APP_KEY", default="") or ""
    access_key = (
        ps_get_str("access_key", env="BYTEDANCE_ASR_ACCESS_KEY", default="") or ""
    )
    if app_key and access_key:
        headers["X-Api-App-Key"] = app_key
        headers["X-Api-Access-Key"] = access_key
        return headers
    raise RuntimeError(
        "ByteDance Doubao ASR credentials are missing. Set BYTEDANCE_ASR_API_KEY "
        "or configure api_key in config/providers/bytedance.yaml."
    )


def _header(
    message_type: int,
    flags: int,
    serialization: int,
    compression: int,
) -> bytes:
    return bytes(
        (
            0x11,
            (message_type << 4) | flags,
            (serialization << 4) | compression,
            0x00,
        )
    )


def build_full_request(payload: dict[str, Any]) -> bytes:
    data = gzip.compress(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
    return (
        _header(_FULL_CLIENT_REQUEST, _NO_SEQUENCE, _JSON, _GZIP)
        + struct.pack(">I", len(data))
        + data
    )


def build_audio_request(audio: bytes, *, final: bool = False) -> bytes:
    data = gzip.compress(audio)
    flags = _LAST_PACKET if final else _NO_SEQUENCE
    return (
        _header(_AUDIO_ONLY_REQUEST, flags, _NO_SERIALIZATION, _GZIP)
        + struct.pack(">I", len(data))
        + data
    )


def parse_server_message(message: bytes) -> tuple[dict[str, Any], bool, int | None]:
    if len(message) < 4:
        raise ByteDanceProtocolError(0, "response header is truncated")
    header_size = (message[0] & 0x0F) * 4
    message_type = message[1] >> 4
    flags = message[1] & 0x0F
    compression = message[2] & 0x0F
    if len(message) < header_size:
        raise ByteDanceProtocolError(0, "response header extension is truncated")
    offset = header_size

    if message_type == _ERROR_RESPONSE:
        if len(message) < offset + 8:
            raise ByteDanceProtocolError(0, "error response is truncated")
        code, size = struct.unpack_from(">II", message, offset)
        raw = message[offset + 8 : offset + 8 + size]
        if compression == _GZIP:
            raw = gzip.decompress(raw)
        try:
            payload = json.loads(raw.decode("utf-8"))
            detail = payload.get("message") if isinstance(payload, dict) else payload
        except Exception:
            detail = raw.decode("utf-8", errors="replace")
        raise ByteDanceProtocolError(code, str(detail or "unknown upstream error"))

    if message_type != _FULL_SERVER_RESPONSE:
        return {}, False, None

    sequence: int | None = None
    if flags & 0x1:
        if len(message) < offset + 4:
            raise ByteDanceProtocolError(0, "response sequence is truncated")
        sequence = struct.unpack_from(">i", message, offset)[0]
        offset += 4
    if len(message) < offset + 4:
        raise ByteDanceProtocolError(0, "response payload size is missing")
    size = struct.unpack_from(">I", message, offset)[0]
    raw = message[offset + 4 : offset + 4 + size]
    if len(raw) != size:
        raise ByteDanceProtocolError(0, "response payload is truncated")
    if compression == _GZIP and raw:
        raw = gzip.decompress(raw)
    payload = json.loads(raw.decode("utf-8")) if raw else {}
    if not isinstance(payload, dict):
        payload = {}
    return (
        payload,
        bool(flags & _LAST_PACKET) or bool(sequence and sequence < 0),
        sequence,
    )


def extract_transcript(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    result = payload.get("result")
    if isinstance(result, dict):
        text = result.get("text")
        if isinstance(text, str):
            return text.strip()
    if isinstance(result, list):
        texts = [
            str(item.get("text") or "").strip()
            for item in result
            if isinstance(item, dict)
        ]
        return "".join(text for text in texts if text)
    return ""


def build_request_payload(*, audio_format: str = "pcm") -> dict[str, Any]:
    request: dict[str, Any] = {
        "model_name": "bigmodel",
        "enable_itn": ps_get_bool("enable_itn", default=True),
        "enable_punc": ps_get_bool("enable_punc", default=True),
        "enable_ddc": ps_get_bool("enable_ddc", default=False),
        "enable_nonstream": ps_get_bool("enable_nonstream", default=True),
        "show_utterances": True,
        "result_type": "full",
        "ssd_version": "200",
    }
    end_window_size = ps_get_int("end_window_size", default=800)
    if end_window_size is not None:
        request["end_window_size"] = max(200, int(end_window_size))
    language = ps_get_str("language", env="BYTEDANCE_ASR_LANGUAGE", default=None)
    audio: dict[str, Any] = {
        "format": audio_format,
        "codec": "raw",
        "rate": get_sample_rate(),
        "bits": 16,
        "channel": 1,
    }
    if language:
        audio["language"] = language
    return {
        "user": {"uid": str(uuid.uuid4())},
        "audio": audio,
        "request": request,
    }


def downmix_resample_to_pcm16(audio_chunk: np.ndarray) -> bytes:
    if audio_chunk.size == 0:
        return b""
    mono = audio_chunk.mean(axis=1) if audio_chunk.ndim == 2 else audio_chunk
    mono = np.nan_to_num(mono, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
    mono = np.clip(mono, -1.0, 1.0)
    mono = mono[::3]
    return (mono * 32767.0).astype("<i2", copy=False).tobytes()


async def _receive_responses(
    websocket: Any,
    on_text: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[str, int | None]:
    latest_text = ""
    last_sequence: int | None = None
    while True:
        message = await websocket.recv()
        if not isinstance(message, bytes):
            continue
        payload, is_final, sequence = parse_server_message(message)
        last_sequence = sequence if sequence is not None else last_sequence
        text = extract_transcript(payload)
        if text and text != latest_text:
            latest_text = text
            if on_text is not None:
                await on_text(text)
        if is_final:
            return latest_text, last_sequence


class ByteDanceStreamingSession(StreamingTranscriptionSession):
    def __init__(self, task_id: str, time_start: float) -> None:
        self.task_id = task_id
        self.time_start = time_start
        self.record_stop = time_start
        self.t_submit = 0.0
        self.t_complete = 0.0
        self._connect_id = str(uuid.uuid4())
        self._connection: Any = None
        self._websocket: Any = None
        self._receiver: asyncio.Task[tuple[str, int | None]] | None = None
        self._audio_buffer = bytearray()
        self._chunk_bytes = get_sample_rate() * 2 * get_chunk_ms() // 1000
        self._audio_bytes_sent = 0
        self._last_text = ""
        self._emitted_deltas = False
        self._closed = False
        self._log_id: str | None = None

    async def start(self) -> None:
        if self._websocket is not None:
            return
        self._connection = websockets.connect(
            get_url(),
            extra_headers=build_headers(self._connect_id),
            open_timeout=get_timeout_seconds(),
            close_timeout=5,
            max_size=8 * 1024 * 1024,
        )
        self._websocket = await self._connection
        response_headers = getattr(self._websocket, "response_headers", {})
        self._log_id = response_headers.get("X-Tt-Logid") if response_headers else None
        self.t_submit = time.time()
        await self._websocket.send(build_full_request(build_request_payload()))
        self._receiver = asyncio.create_task(
            _receive_responses(self._websocket, self._handle_text),
            name=f"bytedance_stream_recv:{self.task_id}",
        )

    async def send_audio(self, audio_chunk: np.ndarray) -> None:
        if self._websocket is None:
            await self.start()
        pcm = downmix_resample_to_pcm16(audio_chunk)
        self._audio_buffer.extend(pcm)
        while len(self._audio_buffer) >= self._chunk_bytes:
            frame = bytes(self._audio_buffer[: self._chunk_bytes])
            del self._audio_buffer[: self._chunk_bytes]
            await self._send_frame(frame)

    async def _send_frame(self, frame: bytes) -> None:
        if self._websocket is None or not frame:
            return
        await self._websocket.send(build_audio_request(frame))
        self._audio_bytes_sent += len(frame)

    async def _handle_text(self, text: str) -> None:
        self._last_text = text
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
            return "", 204, now, now, self._metadata(None)
        if self._audio_buffer:
            await self._send_frame(bytes(self._audio_buffer))
            self._audio_buffer.clear()
        try:
            await self._websocket.send(build_audio_request(b"", final=True))
            text, sequence = await asyncio.wait_for(
                self._receiver, timeout=get_timeout_seconds()
            )
            self._last_text = text or self._last_text
            status = 200 if self._last_text else 204
            error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            status = 502
            sequence = None
            error = f"{exc.__class__.__name__}: {exc}"
        self.t_complete = time.time()
        await self._close()
        return (
            self._last_text if status == 200 else "",
            status,
            self.t_submit,
            self.t_complete,
            self._metadata(sequence, error=error),
        )

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

    def _metadata(
        self, sequence: int | None, *, error: str | None = None
    ) -> Dict[str, Any]:
        return {
            "provider": "bytedance",
            "via": "doubao-asr-2.0-websocket",
            "resource_id": get_resource_id(),
            "connect_id": self._connect_id,
            "log_id": self._log_id,
            "sequence": sequence,
            "sample_rate": get_sample_rate(),
            "audio_bytes_sent": self._audio_bytes_sent,
            "streaming": True,
            "emitted_deltas": self._emitted_deltas,
            "error": error,
        }
