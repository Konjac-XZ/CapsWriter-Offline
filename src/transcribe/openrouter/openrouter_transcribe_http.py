from __future__ import annotations

import asyncio
import base64
import io
import json
import random
import time
from typing import Any, Optional, Tuple

import httpx

from src.infra.cosmic import Cosmic, console
from src.infra.user_lexicon import load_words
from src.provider.provider_settings import (
    get_float as ps_get_float,
    get_context_prompt as ps_get_context_prompt,
    get_prompt as ps_get_prompt,
    get_str as ps_get_str,
    get_value as ps_get_value,
)

_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_HTTP2_ENABLED = True
_REQUEST_SEQ = 0


def _clean_base_url(url: str) -> str:
    return (url or "").strip().rstrip("/")


def get_api_key() -> str:
    return ps_get_str("api_key", env="OPENROUTER_API_KEY", default="") or ""


def get_model() -> str:
    return (
        ps_get_str("model", env="OPENROUTER_TRANSCRIBE_MODEL", default="google/chirp-3")
        or "google/chirp-3"
    )


def get_base_url() -> str:
    default = "https://openrouter.ai"
    return _clean_base_url(
        ps_get_str("base_url", env="OPENROUTER_BASE_URL", default=default) or default
    )


def get_language() -> str | None:
    return ps_get_str("language", env="OPENROUTER_TRANSCRIBE_LANGUAGE", default=None)


def get_temperature() -> float | None:
    return ps_get_float(
        "temperature", env="OPENROUTER_TRANSCRIBE_TEMPERATURE", default=None
    )


def get_timeout_seconds() -> float:
    raw = ps_get_float(
        "timeout_seconds", env="OPENROUTER_TIMEOUT_SECONDS", default=30.0
    )
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
    return (
        ps_get_str(
            "prompt_provider_slug",
            env="OPENROUTER_PROMPT_PROVIDER_SLUG",
            default="google-vertex",
        )
        or "google-vertex"
    )


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


def infer_prompt_option_shape(
    model: str | None = None, provider_slug: str | None = None
) -> str:
    shape = get_prompt_option_shape()
    if shape != "auto":
        return shape

    model_name = (model or get_model()).strip().lower()
    slug = (provider_slug or get_prompt_provider_slug()).strip().lower()
    if model_name == "openai/gpt-transcribe":
        return "gpt_transcribe_context"
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
    if shape in {
        "prompt",
        "flat",
        "simple",
        "openai",
        "openai_transcription",
        "openai_transcriptions",
    }:
        return {"prompt": prompt}
    if shape in {"custom_prompt", "customprompt"}:
        return {"customPrompt": prompt}
    if shape in {"custom_prompt_config", "custompromptconfig"}:
        return {"customPromptConfig": {"customPrompt": prompt}}

    return build_google_speech_v2_options(prompt)


def get_context_languages() -> list[str]:
    raw = ps_get_value("languages", env=None, default=["zh-cn", "en"])
    if isinstance(raw, str):
        values = raw.split(",")
    elif isinstance(raw, (list, tuple)):
        values = raw
    else:
        return []
    return [str(value).strip().lower() for value in values if str(value).strip()]


def get_context_keywords() -> list[str]:
    keywords: list[str] = []
    for value in load_words():
        keyword = str(value).strip()
        if keyword and not any(char in keyword for char in "<>\r\n"):
            keywords.append(keyword)
    return list(dict.fromkeys(keywords))


def build_gpt_transcribe_context_options() -> dict[str, Any]:
    options: dict[str, Any] = {}
    if should_send_prompt():
        prompt = ps_get_context_prompt().strip()
        if prompt:
            options["prompt"] = prompt
        keywords = get_context_keywords()
        if keywords:
            options["keywords"] = keywords
    languages = get_context_languages()
    if languages:
        options["languages"] = languages
    return options


def build_provider_options() -> dict[str, Any] | None:
    shape = infer_prompt_option_shape()
    if shape == "gpt_transcribe_context":
        return build_gpt_transcribe_context_options() or None

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
    model = get_model()
    body: dict[str, Any] = {
        "model": model,
        "input_audio": {
            "data": audio_b64,
            "format": infer_audio_format(payload_mime),
        },
        "stream": True,
    }

    temperature = get_temperature()
    if temperature is not None:
        body["temperature"] = float(temperature)

    if model.strip().lower() != "openai/gpt-transcribe":
        language = get_language()
        if language:
            body["language"] = language

    provider_options = build_provider_options()
    if provider_options:
        body["provider"] = {"options": {get_prompt_provider_slug(): provider_options}}

    return body


def _extract_stream_text(event: Any) -> tuple[str | None, bool]:
    """Return (text, is_append_only_delta) from an OpenRouter STT event."""
    if isinstance(event, str):
        return event, False
    if not isinstance(event, dict):
        return None, False

    event_type = str(event.get("type") or "")
    if event_type == "transcript.text.delta" and isinstance(event.get("delta"), str):
        return event["delta"], True
    if event_type == "transcript.text.done" and isinstance(event.get("text"), str):
        return event["text"], False
    if isinstance(event.get("delta"), str):
        return event["delta"], True
    if isinstance(event.get("text"), str):
        return event["text"], False
    return None, False


async def _emit_transcript_delta(
    task_id: str,
    text: str,
    time_start: float,
    record_stop: float,
    t_submit: float,
) -> None:
    await Cosmic.queue_out.put(
        {
            "task_id": task_id,
            "is_final": False,
            "text": text,
            "time_start": time_start,
            "time_stop": record_stop,
            "time_submit": t_submit,
            "time_complete": time.time(),
            "source": "mic",
            "is_transcript_delta": True,
            "stream": True,
        }
    )


async def stream_transcribe(
    client: httpx.AsyncClient,
    url: str,
    headers: dict[str, str],
    request_body: dict[str, Any],
    task_id: str,
    time_start: float,
    record_stop: float,
) -> tuple[str, int, float, float, bool]:
    t_submit = time.time()
    current_text = ""
    last_emit = 0.0
    last_emitted_text = ""
    saw_delta = False

    async with client.stream("POST", url, headers=headers, json=request_body) as resp:
        status_code = resp.status_code
        http2_flag = resp.http_version == "HTTP/2"
        if not 200 <= status_code < 300:
            await resp.aread()
            return _error_text(resp), status_code, t_submit, time.time(), http2_flag

        async for raw_line in resp.aiter_lines():
            line = raw_line.strip()
            if not line or line.startswith(":") or line.startswith("event:"):
                continue
            if line.startswith("data:"):
                line = line[5:].strip()
            if line in {"[DONE]", "DONE"}:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(event, dict) and event.get("error"):
                status_code = 502
                current_text = _compact_json(event["error"])
                break
            text_part, is_delta = _extract_stream_text(event)
            if not text_part:
                continue
            saw_delta = saw_delta or is_delta
            current_text = current_text + text_part if is_delta else text_part

            now = time.time()
            if is_delta and current_text and now - last_emit >= 0.05:
                last_emit = now
                await _emit_transcript_delta(
                    task_id, current_text, time_start, record_stop, t_submit
                )
                last_emitted_text = current_text

        if saw_delta and current_text and current_text != last_emitted_text:
            await _emit_transcript_delta(
                task_id, current_text, time_start, record_stop, t_submit
            )

    return current_text, status_code, t_submit, time.time(), http2_flag


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
        provider = request_body.get("provider")
        option_summary: dict[str, Any] = {}
        if isinstance(provider, dict) and isinstance(provider.get("options"), dict):
            for slug, options in provider["options"].items():
                if not isinstance(options, dict):
                    continue
                option_summary[str(slug)] = {
                    "keys": sorted(str(key) for key in options),
                    "keywords_count": (
                        len(options.get("keywords", []))
                        if isinstance(options.get("keywords"), list)
                        else 0
                    ),
                }
        summary["provider"] = {"options": option_summary}
    if "temperature" in request_body:
        summary["temperature"] = request_body.get("temperature")
    return summary


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
        t_submit = time.time()
        try:
            console.print(
                f"[openrouter:{request_id}] dispatch attempt={attempt + 1} url={url} task_id={task_id}",
                style="bright_black",
            )
            (
                text_out,
                last_status,
                t_submit,
                t_complete,
                http2_flag,
            ) = await stream_transcribe(
                client,
                url,
                headers,
                request_body,
                task_id,
                time_start,
                record_stop,
            )
            if 200 <= last_status < 300:
                console.print(
                    f"[openrouter:{request_id}] success status={last_status} "
                    f"elapsed_ms={(t_complete - t_submit) * 1000:.1f} text_len={len(text_out)}",
                    style="bright_black",
                )
                return text_out, last_status, t_submit, t_complete, http2_flag

            last_text = text_out
            if not _should_retry(last_status):
                console.print(
                    f"OpenRouter 服务响应错误：{last_status} {last_text}",
                    style="bright_red",
                )
                console.print(
                    f"[openrouter:{request_id}] request_summary {_compact_json(_safe_request_summary(request_body))}",
                    style="bright_yellow",
                )
                return last_text, last_status, t_submit, t_complete, http2_flag
        except (
            httpx.ReadTimeout,
            httpx.ConnectTimeout,
            httpx.ConnectError,
            httpx.RemoteProtocolError,
            httpx.HTTPError,
            OSError,
        ) as exc:
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
