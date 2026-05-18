from __future__ import annotations

import asyncio
import base64
import io
import json
import random
import time
from typing import Any, Optional, Tuple

import httpx

from src.infra.cosmic import console
from src.infra.response_parse import extract_text_from_body
from src.provider.provider_settings import (
    get_float as ps_get_float,
    get_prompt as ps_get_prompt,
    get_str as ps_get_str,
)

_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_HTTP2_ENABLED = True
_REQUEST_SEQ = 0


def _clean_base_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def get_api_key() -> str:
    return ps_get_str("api_key", env="OPENROUTER_API_KEY", default="") or ""


def get_model() -> str:
    return ps_get_str("model", env="OPENROUTER_TRANSCRIBE_MODEL", default="google/chirp-3") or "google/chirp-3"


def get_base_url() -> str:
    default = "https://openrouter.ai"
    return _clean_base_url(ps_get_str("base_url", env="OPENROUTER_BASE_URL", default=default) or default)


def get_language() -> str | None:
    return ps_get_str("language", env="OPENROUTER_TRANSCRIBE_LANGUAGE", default=None)


def get_temperature() -> float | None:
    return ps_get_float("temperature", env="OPENROUTER_TRANSCRIBE_TEMPERATURE", default=None)


def get_timeout_seconds() -> float:
    raw = ps_get_float("timeout_seconds", env="OPENROUTER_TIMEOUT_SECONDS", default=30.0)
    try:
        return min(30.0, max(1.0, float(raw or 30.0)))
    except Exception:
        return 30.0


def get_site_url() -> str | None:
    return ps_get_str("site_url", env="OPENROUTER_SITE_URL", default=None)


def get_site_name() -> str | None:
    return ps_get_str("site_name", env="OPENROUTER_SITE_NAME", default=None)


def get_configured_audio_format() -> str | None:
    return ps_get_str("audio_format", env="OPENROUTER_AUDIO_FORMAT", default="auto")


def get_prompt_provider_slug() -> str:
    return ps_get_str(
        "prompt_provider_slug",
        env="OPENROUTER_PROMPT_PROVIDER_SLUG",
        default="google-vertex",
    ) or "google-vertex"


def should_send_prompt() -> bool:
    raw = ps_get_str("send_prompt", env="OPENROUTER_SEND_PROMPT", default="true")
    return (raw or "").strip().lower() in {"1", "true", "yes", "on"}


def get_prompt_option_shape() -> str:
    shape = ps_get_str(
        "prompt_option_shape",
        env="OPENROUTER_PROMPT_OPTION_SHAPE",
        default="auto",
    )
    return (shape or "auto").strip().lower()


def infer_prompt_option_shape(model: str | None = None, provider_slug: str | None = None) -> str:
    shape = get_prompt_option_shape()
    if shape != "auto":
        return shape

    model_name = (model or get_model()).strip().lower()
    slug = (provider_slug or get_prompt_provider_slug()).strip().lower()
    if model_name.startswith("openai/") or slug == "openai":
        return "openai_transcription"
    if model_name.startswith("google/") or slug == "google-vertex":
        return "google_speech_v2"
    return "flat"


def _merge_nested_dict(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _merge_nested_dict(base[key], value)
        else:
            base[key] = value
    return base


def build_google_speech_v2_options(prompt: str | None = None) -> dict[str, Any]:
    config: dict[str, Any] = {}
    if prompt:
        config["features"] = {
            "customPromptConfig": {
                "customPrompt": prompt,
            }
        }
    return {"config": config} if config else {}


def build_prompt_provider_options(prompt: str) -> dict[str, Any]:
    shape = infer_prompt_option_shape()
    if shape in {"prompt", "flat", "simple", "openai", "openai_transcription", "openai_transcriptions"}:
        return {"prompt": prompt}
    if shape in {"custom_prompt", "customprompt"}:
        return {"customPrompt": prompt}
    if shape in {"custom_prompt_config", "custompromptconfig"}:
        return {"customPromptConfig": {"customPrompt": prompt}}

    return build_google_speech_v2_options(prompt)


def build_provider_options() -> dict[str, Any] | None:
    shape = infer_prompt_option_shape()
    options: dict[str, Any] = {}
    if shape == "google_speech_v2":
        options = build_google_speech_v2_options()

    if should_send_prompt():
        prompt = ps_get_prompt()
        if prompt:
            _merge_nested_dict(options, build_prompt_provider_options(prompt))

    return options or None


def build_headers(api_key: str) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    site_url = get_site_url()
    if site_url:
        headers["HTTP-Referer"] = site_url
    site_name = get_site_name()
    if site_name:
        headers["X-OpenRouter-Title"] = site_name
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


def _next_request_id() -> str:
    global _REQUEST_SEQ
    _REQUEST_SEQ += 1
    return f"or-{_REQUEST_SEQ:05d}"


def _encode_audio(payload_buf: io.BytesIO) -> str:
    if hasattr(payload_buf, "seek"):
        payload_buf.seek(0)
    raw = payload_buf.read()
    return base64.b64encode(raw).decode("ascii")


def infer_audio_format(payload_mime: str) -> str:
    mime = (payload_mime or "").strip().lower()
    configured = (get_configured_audio_format() or "auto").strip().lower()
    if configured and configured != "auto":
        return configured
    if mime in {"audio/mpeg", "audio/mp3", "audio/x-mpeg"}:
        return "mp3"
    if mime in {"audio/wav", "audio/wave", "audio/x-wav"}:
        return "wav"
    return "wav"


def build_request_body(payload_mime: str, audio_b64: str) -> dict[str, Any]:
    body: dict[str, Any] = {
        "model": get_model(),
        "input_audio": {
            "data": audio_b64,
            "format": infer_audio_format(payload_mime),
        },
    }

    language = get_language()
    if language:
        body["language"] = language

    temperature = get_temperature()
    if temperature is not None:
        body["temperature"] = float(temperature)

    provider_options = build_provider_options()
    if provider_options:
        body["provider"] = {
            "options": {
                get_prompt_provider_slug(): provider_options
            }
        }

    return body


def _error_text(resp: Optional[httpx.Response]) -> str:
    if resp is None:
        return "OpenRouter request failed: no response"
    try:
        data = resp.json()
        if isinstance(data, dict):
            err = data.get("error")
            if isinstance(err, dict):
                message = err.get("message")
                if message:
                    return str(message)
            message = data.get("message")
            if message:
                return str(message)
    except Exception:
        pass
    return resp.text.strip()


def _compact_json(value: Any, max_chars: int = 4000) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        text = str(value)
    if len(text) > max_chars:
        return text[: max_chars - 3] + "..."
    return text


def _safe_request_summary(request_body: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "model": request_body.get("model"),
        "language": request_body.get("language"),
    }
    input_audio = request_body.get("input_audio")
    if isinstance(input_audio, dict):
        data = input_audio.get("data")
        summary["input_audio"] = {
            "format": input_audio.get("format"),
            "data_b64_len": len(data) if isinstance(data, str) else None,
        }
    if "provider" in request_body:
        summary["provider"] = request_body.get("provider")
    if "temperature" in request_body:
        summary["temperature"] = request_body.get("temperature")
    return summary


def _response_detail(resp: httpx.Response) -> str:
    body = resp.text.strip()
    try:
        data = resp.json()
    except Exception:
        data = None

    details: list[str] = []
    try:
        request_id = resp.headers.get("x-request-id") or resp.headers.get("cf-ray")
        if request_id:
            details.append(f"request_id={request_id}")
    except Exception:
        pass

    if isinstance(data, dict):
        details.append(f"json={_compact_json(data)}")
        error = data.get("error")
        if isinstance(error, dict):
            metadata = error.get("metadata")
            if isinstance(metadata, dict):
                raw = metadata.get("raw") or metadata.get("provider_response") or metadata.get("body")
                if raw:
                    details.append(f"provider_raw={raw}")
    elif body:
        details.append(f"body={body[:4000]}")

    return " | ".join(details)


def _extract_text(resp: httpx.Response) -> str:
    parsed = extract_text_from_body(resp.text)
    if isinstance(parsed, str) and parsed.strip():
        return parsed
    try:
        data = resp.json()
    except Exception:
        return resp.text.strip()
    if isinstance(data, dict) and isinstance(data.get("text"), str):
        return data["text"]
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
            "OpenRouter API key is missing. Set OPENROUTER_API_KEY or fill config/providers/openrouter.yaml.",
            0,
            now,
            now,
            False,
        )

    url = f"{get_base_url()}/api/v1/audio/transcriptions"
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
        request_id = _next_request_id()
        client = get_http_client()
        resp: Optional[httpx.Response] = None
        t_submit = time.time()
        try:
            console.print(
                f"[openrouter:{request_id}] dispatch attempt={attempt + 1} url={url} task_id={task_id}",
                style="bright_black",
            )
            resp = await client.post(url, headers=headers, json=request_body)
            t_complete = time.time()
            last_status = resp.status_code
            http2_flag = resp.http_version == "HTTP/2"
            if 200 <= resp.status_code < 300:
                text_out = _extract_text(resp)
                console.print(
                    f"[openrouter:{request_id}] success status={resp.status_code} "
                    f"elapsed_ms={(t_complete - t_submit) * 1000:.1f} text_len={len(text_out)}",
                    style="bright_black",
                )
                return text_out, resp.status_code, t_submit, t_complete, http2_flag

            last_text = _error_text(resp)
            if not _should_retry(resp.status_code):
                console.print(f"OpenRouter 服务响应错误：{resp.status_code} {last_text}", style="bright_red")
                console.print(
                    f"[openrouter:{request_id}] response_detail {_response_detail(resp)}",
                    style="bright_red",
                )
                console.print(
                    f"[openrouter:{request_id}] request_summary {_compact_json(_safe_request_summary(request_body))}",
                    style="bright_yellow",
                )
                return last_text, resp.status_code, t_submit, t_complete, http2_flag
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError, httpx.RemoteProtocolError, httpx.HTTPError, OSError) as exc:
            t_complete = time.time()
            last_status = 0
            last_text = f"OpenRouter request error: {exc}"
            try:
                await close_http_client()
            except Exception:
                pass

        if attempt + 1 < retry_count and _should_retry(last_status):
            delay = base_delay * (2**attempt) + random.uniform(0.0, 0.1)
            console.print(
                f"OpenRouter 网络或服务异常（第 {attempt + 1}/{retry_count} 次）：{last_text}",
                style="bright_yellow",
            )
            await asyncio.sleep(delay)
            continue

        return last_text, last_status, t_submit, t_complete, http2_flag

    return last_text, last_status, t_submit, t_complete, http2_flag


def http2_enabled() -> bool:
    return _HTTP2_ENABLED
