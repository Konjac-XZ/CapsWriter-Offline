from __future__ import annotations

import asyncio
import base64
import io
import time
from typing import Any, Dict, Optional, Tuple

import httpx

from util.provider_settings import get_float as ps_get_float
from util.provider_settings import get_prompt as ps_get_prompt
from util.provider_settings import get_str as ps_get_str

_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_HTTP2_ENABLED = True


def _clean_base_url(url: str) -> str:
    url = (url or "").strip()
    if url.endswith("/"):
        return url[:-1]
    return url


def get_api_key() -> str:
    return ps_get_str("api_key", env=("GEMINI_API_KEY", "GOOGLE_API_KEY"), default="") or ""


def get_model() -> str:
    return ps_get_str("model", env="GEMINI_MODEL", default="gemini-2.5-flash") or "gemini-2.5-flash"


def get_base_url() -> str:
    default = "https://generativelanguage.googleapis.com"
    return _clean_base_url(ps_get_str("base_url", env="GEMINI_BASE_URL", default=default) or default)


def get_timeout_seconds() -> float:
    return float(ps_get_float("timeout_seconds", env="GEMINI_TIMEOUT_SECONDS", default=120.0) or 120.0)


def _is_gemini3(model: str) -> bool:
    return "gemini-3" in (model or "").lower()


def _get_thinking_level() -> Optional[str]:
    level = ps_get_str("thinking_level", env="GEMINI_THINKING_LEVEL", default=None)
    if not level:
        return None
    normalized = level.strip().lower()
    allowed = {"minimal", "low", "medium", "high"}
    return normalized if normalized in allowed else None


def build_headers(api_key: str) -> Dict[str, str]:
    return {
        "Content-Type": "application/json",
        "x-goog-api-key": api_key,
    }


def build_limits() -> httpx.Limits:
    return httpx.Limits(
        max_keepalive_connections=10,
        max_connections=20,
    )


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


def _extract_text(resp_json: Dict[str, Any]) -> str:
    candidates = resp_json.get("candidates") or []
    if not candidates:
        return ""
    first = candidates[0] or {}
    content = first.get("content") or {}
    parts = content.get("parts") or []
    texts = [p.get("text", "") for p in parts if isinstance(p, dict) and p.get("text")]
    return "\n".join([t for t in texts if isinstance(t, str)]).strip()


def _error_text(resp: Optional[httpx.Response]) -> str:
    if resp is None:
        return "Gemini request failed: no response"
    try:
        data = resp.json()
        if isinstance(data, dict):
            # Prefer explicit message fields if present
            err = data.get("error") or {}
            message = err.get("message")
            if message:
                return str(message)
            msg = data.get("message")
            if msg:
                return str(msg)
    except Exception:
        pass
    return resp.text.strip()


def _build_request_body(
    payload_mime: str,
    audio_b64: str,
    prompt_text: str,
    model: str,
) -> Dict[str, Any]:
    parts = []
    text_prompt = prompt_text.strip() or "Generate a transcript of the speech."
    parts.append({"text": text_prompt})
    parts.append(
        {
            "inlineData": {
                "mimeType": payload_mime,
                "data": audio_b64,
            }
        }
    )

    request: Dict[str, Any] = {"contents": [{"role": "user", "parts": parts}]}

    generation_config: Dict[str, Any] = {}
    temperature = ps_get_float("temperature", env="TRANSCRIBE_TEMPERATURE", default=None)
    if temperature is not None:
        generation_config["temperature"] = float(temperature)

    # Only send thinking_level for Gemini 3 series models.
    if _is_gemini3(model):
        thinking_level = _get_thinking_level()
        if thinking_level:
            generation_config["thinkingConfig"] = {"thinkingLevel": thinking_level}

    if generation_config:
        request["generationConfig"] = generation_config
        


    return request


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
    """
    Send audio to Gemini and return (text, status_code, t_submit, t_complete, http2_flag).
    """
    api_key = get_api_key()
    if not api_key:
        return "Gemini API key is missing. Set GEMINI_API_KEY or fill config/providers/gemini.yaml.", 0, time.time(), time.time(), False

    model = get_model()
    base_url = get_base_url()
    url = f"{base_url}/v1beta/models/{model}:generateContent"

    audio_b64 = _encode_audio(payload_buf)
    prompt_text = ps_get_prompt()
    request_body = _build_request_body(payload_mime, audio_b64, prompt_text, model)
    headers = build_headers(api_key)

    client = get_http_client()
    last_text = ""
    last_status = 0
    http2_flag = False

    for attempt in range(max_retries + 1):
        t_submit = time.time()
        resp: Optional[httpx.Response] = None
        try:
            resp = await client.post(url, headers=headers, json=request_body)
            last_status = resp.status_code
            http2_flag = resp.http_version == "HTTP/2"
            if resp.status_code == 200:
                text_out = _extract_text(resp.json())
                t_complete = time.time()
                return text_out, resp.status_code, t_submit, t_complete, http2_flag

            last_text = _error_text(resp)
        except Exception as exc:  # pragma: no cover - defensive network handling
            last_text = f"Gemini request error: {exc}"
            last_status = 0
        t_complete = time.time()

        if attempt < max_retries and _should_retry(last_status):
            await asyncio.sleep(base_delay * (2**attempt))
            continue

        return last_text, last_status, t_submit, t_complete, http2_flag

    now = time.time()
    return last_text, last_status, now, now, http2_flag


def http2_enabled() -> bool:
    return _HTTP2_ENABLED
