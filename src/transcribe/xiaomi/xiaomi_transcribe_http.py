from __future__ import annotations

import asyncio
import base64
import io
import time
from typing import Any, Optional, Tuple

import httpx

from src.infra.response_parse import extract_text_from_body
from src.provider.provider_settings import (
    get_prompt as ps_get_prompt,
    get_str as ps_get_str,
)

_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_HTTP2_ENABLED = True


def _clean_base_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def get_api_key() -> str:
    return ps_get_str("api_key", env="XIAOMI_API_KEY", default="") or ""


def get_model() -> str:
    return ps_get_str("model", env="XIAOMI_TRANSCRIBE_MODEL", default="mimo-v2.5-asr") or "mimo-v2.5-asr"


def get_base_url() -> str:
    default = "https://api.xiaomimimo.com"
    return _clean_base_url(ps_get_str("base_url", env="XIAOMI_BASE_URL", default=default) or default)


def get_timeout_seconds() -> float:
    raw = ps_get_str("timeout_seconds", env="XIAOMI_TIMEOUT_SECONDS", default="30")
    try:
        return min(30.0, max(1.0, float(raw or 30.0)))
    except Exception:
        return 30.0


def get_auth_header_mode() -> str:
    mode = ps_get_str("auth_header", env="XIAOMI_AUTH_HEADER", default="api-key") or "api-key"
    normalized = mode.strip().lower().replace("_", "-")
    if normalized in {"bearer", "authorization"}:
        return "bearer"
    return "api-key"


def get_language() -> str:
    language = ps_get_str("language", env="XIAOMI_TRANSCRIBE_LANGUAGE", default="auto") or "auto"
    normalized = language.strip().lower()
    return normalized if normalized in {"auto", "zh", "en"} else "auto"


def should_send_prompt() -> bool:
    raw = ps_get_str("send_prompt", env="XIAOMI_SEND_PROMPT", default="false") or "false"
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def build_headers(api_key: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if get_auth_header_mode() == "bearer":
        headers["Authorization"] = f"Bearer {api_key}"
    else:
        headers["api-key"] = api_key
    return headers


def build_limits() -> httpx.Limits:
    return httpx.Limits(max_keepalive_connections=5, max_connections=10)


def get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(
            http2=_HTTP2_ENABLED,
            timeout=httpx.Timeout(get_timeout_seconds()),
            limits=build_limits(),
        )
    return _HTTP_CLIENT


async def close_http_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


def _encode_audio(payload_buf: io.BytesIO) -> str:
    if hasattr(payload_buf, "seek"):
        payload_buf.seek(0)
    raw = payload_buf.read()
    return base64.b64encode(raw).decode("ascii")


def infer_audio_format(payload_mime: str) -> str:
    mime = (payload_mime or "").strip().lower()
    if mime in {"audio/mpeg", "audio/mp3", "audio/x-mpeg"}:
        return "mp3"
    if mime in {"audio/wav", "audio/wave", "audio/x-wav"}:
        return "wav"
    return "wav"


def build_audio_data_url(payload_mime: str, audio_b64: str) -> str:
    mime = (payload_mime or "").strip() or "audio/wav"
    return f"data:{mime};base64,{audio_b64}"


def build_request_body(payload_mime: str, audio_b64: str) -> dict[str, Any]:
    content: list[dict[str, Any]] = [
        {
            "type": "input_audio",
            "input_audio": {
                "data": build_audio_data_url(payload_mime, audio_b64),
            },
        }
    ]

    if should_send_prompt():
        prompt_text = ps_get_prompt().strip()
        if prompt_text:
            content.insert(
                0,
                {
                    "type": "text",
                    "text": prompt_text,
                },
            )

    body: dict[str, Any] = {
        "model": get_model(),
        "messages": [
            {
                "role": "user",
                "content": content,
            }
        ],
        "asr_options": {
            "language": get_language(),
        },
    }

    return body


def _error_text(resp: Optional[httpx.Response]) -> str:
    if resp is None:
        return "Xiaomi request failed: no response"
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict) and err.get("message"):
                return str(err["message"])
            if isinstance(data.get("message"), str):
                return data["message"]
    except Exception:
        pass
    return resp.text.strip()


def _extract_text(resp: httpx.Response) -> str:
    parsed = extract_text_from_body(resp.text)
    if isinstance(parsed, str) and parsed.strip():
        return parsed.strip()
    try:
        data = resp.json()
    except Exception:
        return resp.text.strip()
    try:
        content = data["choices"][0]["message"].get("content")
        if isinstance(content, str):
            return content.strip()
    except Exception:
        pass
    return resp.text.strip()


def _should_retry(status_code: int) -> bool:
    return status_code in (0, 408, 429, 500, 502, 503, 504)


async def transcribe_with_retries(
    payload_buf: io.BytesIO,
    payload_mime: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
) -> Tuple[str, int, float, float, bool]:
    api_key = get_api_key()
    now = time.time()
    if not api_key:
        return (
            "Xiaomi API key is missing. Set XIAOMI_API_KEY or fill config/providers/xiaomi.yaml.",
            0,
            now,
            now,
            False,
        )

    url = f"{get_base_url()}/v1/chat/completions"
    audio_b64 = _encode_audio(payload_buf)
    request_body = build_request_body(payload_mime, audio_b64)
    headers = build_headers(api_key)

    last_text = ""
    last_status = 0
    http2_flag = False
    t_submit = now
    t_complete = now

    retry_count = max(1, int(max_retries))
    for attempt in range(retry_count):
        resp: Optional[httpx.Response] = None
        t_submit = time.time()
        try:
            resp = await get_http_client().post(url, headers=headers, json=request_body)
            t_complete = time.time()
            last_status = resp.status_code
            http2_flag = resp.http_version == "HTTP/2"
            if 200 <= resp.status_code < 300:
                return _extract_text(resp), resp.status_code, t_submit, t_complete, http2_flag

            last_text = _error_text(resp)
            if not _should_retry(resp.status_code):
                return last_text, resp.status_code, t_submit, t_complete, http2_flag
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError, httpx.RemoteProtocolError, httpx.HTTPError, OSError) as exc:
            t_complete = time.time()
            last_status = 0
            last_text = f"Xiaomi request error: {exc}"
            try:
                await close_http_client()
            except Exception:
                pass

        if attempt + 1 < retry_count and _should_retry(last_status):
            await asyncio.sleep(base_delay * (2**attempt))
            continue

        return last_text, last_status, t_submit, t_complete, http2_flag

    return last_text, last_status, t_submit, t_complete, http2_flag


def http2_enabled() -> bool:
    return _HTTP2_ENABLED
