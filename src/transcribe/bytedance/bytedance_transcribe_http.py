from __future__ import annotations

import asyncio
import base64
import io
import time
import uuid
from typing import Any, Dict, Tuple

import httpx

from src.infra.cosmic import console
from src.provider.provider_settings import (
    get_bool as ps_get_bool,
    get_float as ps_get_float,
    get_str as ps_get_str,
)

_DEFAULT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/recognize/flash"
_DEFAULT_RESOURCE_ID = "volc.bigasr.auc_turbo"
_SUCCESS_CODE = "20000000"
_SILENT_AUDIO_CODE = "20000003"
_MAX_AUDIO_BYTES = 100 * 1024 * 1024


class ByteDanceHTTPError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int = 0,
        api_status_code: str = "",
        log_id: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.api_status_code = api_status_code
        self.log_id = log_id


def get_api_key() -> str:
    return ps_get_str("api_key", env="BYTEDANCE_ASR_API_KEY", default="") or ""


def get_url() -> str:
    return (
        ps_get_str(
            "nonstream_url",
            env="BYTEDANCE_ASR_NONSTREAM_URL",
            default=_DEFAULT_URL,
        )
        or _DEFAULT_URL
    ).rstrip("/")


def get_resource_id() -> str:
    return (
        ps_get_str(
            "nonstream_resource_id",
            env="BYTEDANCE_ASR_NONSTREAM_RESOURCE_ID",
            default=_DEFAULT_RESOURCE_ID,
        )
        or _DEFAULT_RESOURCE_ID
    )


def get_timeout_seconds() -> float:
    value = ps_get_float(
        "nonstream_timeout_seconds",
        env="BYTEDANCE_ASR_NONSTREAM_TIMEOUT_SECONDS",
        default=120.0,
    )
    return max(5.0, min(600.0, float(value or 120.0)))


def build_headers(request_id: str) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Api-Resource-Id": get_resource_id(),
        "X-Api-Request-Id": request_id,
        "X-Api-Sequence": "-1",
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


def _audio_format_from_mime(payload_mime: str) -> str:
    mime = (payload_mime or "").lower()
    if "mpeg" in mime or "mp3" in mime:
        return "mp3"
    if "ogg" in mime or "opus" in mime:
        return "ogg"
    if "wav" in mime or "wave" in mime:
        return "wav"
    if "aac" in mime:
        return "aac"
    if "mp4" in mime or "m4a" in mime:
        return "m4a"
    return "raw"


def build_request_body(
    audio: bytes,
    payload_mime: str,
    *,
    request_id: str,
) -> dict[str, Any]:
    if not audio:
        raise ValueError("ByteDance non-streaming ASR audio payload is empty")
    if len(audio) > _MAX_AUDIO_BYTES:
        raise ValueError(
            "ByteDance non-streaming ASR audio exceeds the 100 MB service limit"
        )

    audio_format = _audio_format_from_mime(payload_mime)
    audio_settings: dict[str, Any] = {
        "data": base64.b64encode(audio).decode("ascii"),
        "format": audio_format,
        "codec": "opus" if audio_format == "ogg" else "raw",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
    }
    language = ps_get_str("language", env="BYTEDANCE_ASR_LANGUAGE", default=None)
    if language:
        audio_settings["language"] = language

    request: dict[str, Any] = {
        "model_name": "bigmodel",
        "enable_itn": ps_get_bool("enable_itn", default=True),
        "enable_punc": ps_get_bool("enable_punc", default=True),
        "enable_ddc": ps_get_bool("enable_ddc", default=False),
        "show_utterances": ps_get_bool("show_utterances", default=True),
    }
    return {
        "user": {"uid": request_id},
        "audio": audio_settings,
        "request": request,
    }


def extract_transcript(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    result = payload.get("result")
    if not isinstance(result, dict):
        body = payload.get("body")
        result = body.get("result") if isinstance(body, dict) else None
    if not isinstance(result, dict):
        return ""
    text = result.get("text")
    return text.strip() if isinstance(text, str) else ""


def _response_metadata(
    response: httpx.Response,
    payload: Any,
    *,
    request_id: str,
    audio_bytes: int,
) -> dict[str, Any]:
    headers = response.headers
    meta: dict[str, Any] = {
        "provider": "bytedance",
        "via": "doubao-recording-flash-http",
        "resource_id": get_resource_id(),
        "request_id": request_id,
        "log_id": headers.get("X-Tt-Logid"),
        "api_status_code": headers.get("X-Api-Status-Code"),
        "api_message": headers.get("X-Api-Message"),
        "audio_bytes": audio_bytes,
        "http_version": response.http_version,
        "streaming": False,
    }
    if isinstance(payload, dict):
        audio_info = payload.get("audio_info")
        if isinstance(audio_info, dict):
            meta["audio_info"] = audio_info
    return meta


def _api_error(response: httpx.Response) -> ByteDanceHTTPError:
    api_code = response.headers.get("X-Api-Status-Code", "")
    api_message = response.headers.get("X-Api-Message", "")
    log_id = response.headers.get("X-Tt-Logid", "")
    detail = api_message.strip()
    if not detail:
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("message") or payload.get("error") or "")
        except Exception:
            detail = response.text.strip()
    parts = [
        f"ByteDance non-streaming ASR failed: HTTP {response.status_code}",
    ]
    if api_code:
        parts.append(f"code={api_code}")
    if detail:
        parts.append(f"message={detail}")
    if log_id:
        parts.append(f"logid={log_id}")
    return ByteDanceHTTPError(
        ", ".join(parts),
        status_code=response.status_code,
        api_status_code=api_code,
        log_id=log_id,
    )


def _should_retry(exc: Exception) -> bool:
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return True
    if not isinstance(exc, ByteDanceHTTPError):
        return False
    if exc.status_code in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    return exc.api_status_code.startswith("55")


async def _transcribe_once(
    client: httpx.AsyncClient,
    audio: bytes,
    payload_mime: str,
) -> tuple[str, int, float, float, dict[str, Any]]:
    request_id = str(uuid.uuid4())
    body = build_request_body(audio, payload_mime, request_id=request_id)
    t_submit = time.time()
    response = await client.post(
        get_url(),
        headers=build_headers(request_id),
        json=body,
    )
    t_complete = time.time()
    try:
        payload = response.json()
    except Exception:
        payload = {}
    api_code = response.headers.get("X-Api-Status-Code", "")
    if response.is_error or api_code not in {_SUCCESS_CODE, _SILENT_AUDIO_CODE}:
        raise _api_error(response)
    text = extract_transcript(payload)
    status = 200 if text else 204
    return (
        text,
        status,
        t_submit,
        t_complete,
        _response_metadata(
            response,
            payload,
            request_id=request_id,
            audio_bytes=len(audio),
        ),
    )


async def transcribe_with_retries(
    payload_buf: io.BytesIO,
    payload_mime: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
) -> Tuple[str, int, float, float, Dict[str, Any]]:
    del task_id, time_start, record_stop
    now = time.time()
    try:
        build_headers(str(uuid.uuid4()))
    except RuntimeError as exc:
        console.print(str(exc), style="bright_red")
        return (
            "",
            0,
            now,
            now,
            {
                "provider": "bytedance",
                "error": str(exc),
            },
        )

    payload_buf.seek(0)
    audio = payload_buf.read()
    try:
        build_request_body(audio, payload_mime, request_id="validation")
    except ValueError as exc:
        return (
            "",
            0,
            now,
            time.time(),
            {
                "provider": "bytedance",
                "error": str(exc),
            },
        )

    attempts = max(1, max_retries + 1)
    timeout = httpx.Timeout(get_timeout_seconds())
    last_error: Exception = RuntimeError("unknown ByteDance ASR error")
    async with httpx.AsyncClient(http2=True, timeout=timeout) as client:
        for attempt in range(attempts):
            try:
                return await _transcribe_once(client, audio, payload_mime)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt + 1 >= attempts or not _should_retry(exc):
                    break
                await asyncio.sleep(base_delay * (2**attempt))

    error = f"{last_error.__class__.__name__}: {last_error}"
    console.print(f"豆包非流式 ASR 转写失败：{error}", style="bright_red")
    status_code = 502
    if isinstance(last_error, ByteDanceHTTPError) and last_error.status_code >= 400:
        status_code = last_error.status_code
    return (
        "",
        status_code,
        now,
        time.time(),
        {
            "provider": "bytedance",
            "resource_id": get_resource_id(),
            "error": error,
            "log_id": (
                last_error.log_id
                if isinstance(last_error, ByteDanceHTTPError)
                else None
            ),
            "api_status_code": (
                last_error.api_status_code
                if isinstance(last_error, ByteDanceHTTPError)
                else None
            ),
        },
    )
