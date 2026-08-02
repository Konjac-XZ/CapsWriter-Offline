"""Qwen Audio Legacy provider settings and request construction."""
import os
from typing import Any, Dict, List

import httpx

from src.infra.cosmic import console
from src.provider.provider_settings import (
    get_bool as ps_get_bool,
    get_prompt as ps_get_prompt,
    get_str as ps_get_str,
)


_STREAM_WARNED: bool = False


def clean_str(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def bool_from_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no"}


def use_sdk() -> bool:
    return bool_from_env("DASHSCOPE_USE_SDK", True)


def should_reuse_http_client() -> bool:
    if ps_get_str("reuse_http_client", env="DASHSCOPE_REUSE_HTTP_CLIENT", default=None) is not None:
        return ps_get_bool("reuse_http_client", env="DASHSCOPE_REUSE_HTTP_CLIENT", default=False)
    return True


def should_use_http2() -> bool:
    if ps_get_str("http2", env="DASHSCOPE_HTTP2", default=None) is not None:
        return ps_get_bool("http2", env="DASHSCOPE_HTTP2", default=False)
    return True


def should_show_debug_logs() -> bool:
    return ps_get_bool("debug", env="DASHSCOPE_DEBUG", default=False)


def should_show_realtime_logs() -> bool:
    if ps_get_str("realtime_debug", env="DASHSCOPE_REALTIME_DEBUG", default=None) is not None:
        return ps_get_bool("realtime_debug", env="DASHSCOPE_REALTIME_DEBUG", default=False)
    return False


def should_emit_realtime_deltas() -> bool:
    return ps_get_bool("realtime_emit_deltas", env="DASHSCOPE_REALTIME_EMIT_DELTAS", default=False)


def get_api_base() -> str:
    base = clean_str(ps_get_str("base_url", env="DASHSCOPE_BASE_URL", default=None))
    if not base:
        base = "https://dashscope.aliyuncs.com"
    return base.rstrip("/")


def get_api_key() -> str:
    key = clean_str(ps_get_str("api_key", env="DASHSCOPE_API_KEY", default=None))
    if not key:
        raise RuntimeError(
            "DASHSCOPE_API_KEY environment variable is required for "
            "provider=qwen-audio-legacy"
        )
    return key


def get_timeout_seconds() -> float:
    raw = ps_get_str("timeout_seconds", env=["DASHSCOPE_TIMEOUT_SECONDS", "OPENAI_HTTP_TIMEOUT"], default="30")
    try:
        return min(30.0, max(1.0, float(raw or "30")))
    except Exception:
        return 30.0


def candidate_endpoints() -> List[str]:
    base = get_api_base()
    override = clean_str(os.getenv("DASHSCOPE_ENDPOINT"))
    path_override = clean_str(os.getenv("DASHSCOPE_ENDPOINT_PATH"))
    candidates: List[str] = []

    def add_endpoint(url: str) -> None:
        if url not in candidates:
            candidates.append(url)

    if override:
        add_endpoint(override)
        return candidates

    if path_override:
        if path_override.startswith("http"):
            add_endpoint(path_override)
        else:
            if not path_override.startswith("/"):
                path_override = "/" + path_override
            add_endpoint(f"{base}{path_override}")

    add_endpoint(f"{base}/api/v1/multimodal_conversation")
    add_endpoint(f"{base}/api/v1/audio/recognitions")
    add_endpoint(f"{base}/api/v1/services/audio/asr/transcriptions")
    return candidates


def get_model() -> str:
    variant = clean_str(ps_get_str("model_variant", env="DASHSCOPE_MODEL_VARIANT", default=None))
    if variant:
        return variant
    model = clean_str(ps_get_str("model", env="DASHSCOPE_MODEL", default=None))
    if model:
        return model
    fallback = clean_str(os.getenv("TRANSCRIBE_MODEL"))
    if fallback:
        return fallback
    return "qwen3-asr-flash"


def get_realtime_model() -> str:
    model = clean_str(ps_get_str("realtime_model", env="DASHSCOPE_REALTIME_MODEL", default=None))
    if model:
        return model
    base_model = get_model()
    if base_model.endswith("-realtime"):
        return base_model
    if base_model.startswith("qwen3-asr-flash"):
        return "qwen3-asr-flash-realtime"
    return base_model


def get_workspace_id() -> str | None:
    return clean_str(
        ps_get_str(
            "workspace_id",
            env="DASHSCOPE_WORKSPACE_ID",
            default=None,
        )
    )


def get_region() -> str:
    return (
        clean_str(ps_get_str("region", env="DASHSCOPE_REGION", default=None))
        or "cn-beijing"
    ).lower()


def get_realtime_url() -> str:
    url = clean_str(ps_get_str("realtime_url", env="DASHSCOPE_REALTIME_URL", default=None))
    if url:
        return url
    workspace_id = get_workspace_id()
    region = get_region()
    if region in {"cn-beijing", "beijing"}:
        if workspace_id:
            return (
                f"wss://{workspace_id}.cn-beijing.maas.aliyuncs.com"
                "/api-ws/v1/realtime"
            )
        return "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    if region in {"ap-southeast-1", "singapore"}:
        if workspace_id:
            return (
                f"wss://{workspace_id}.ap-southeast-1.maas.aliyuncs.com"
                "/api-ws/v1/realtime"
            )
        return "wss://dashscope-intl.aliyuncs.com/api-ws/v1/realtime"
    raise ValueError(
        "qwen-audio-legacy realtime region must be cn-beijing or ap-southeast-1"
    )


def get_realtime_format() -> str:
    return clean_str(ps_get_str("realtime_format", env="DASHSCOPE_REALTIME_FORMAT", default="pcm")) or "pcm"


def get_realtime_sample_rate() -> int:
    raw = ps_get_str("realtime_sample_rate", env="DASHSCOPE_REALTIME_SAMPLE_RATE", default="16000")
    try:
        return int(raw or "16000")
    except Exception:
        return 16000


def get_realtime_enable_vad() -> bool:
    return ps_get_bool("realtime_enable_vad", env="DASHSCOPE_REALTIME_ENABLE_VAD", default=False)


def get_realtime_vad_threshold() -> float:
    raw = ps_get_str("realtime_vad_threshold", env="DASHSCOPE_REALTIME_VAD_THRESHOLD", default="0.0")
    try:
        return max(-1.0, min(1.0, float(raw or "0.0")))
    except Exception:
        return 0.0


def get_realtime_vad_silence_ms() -> int:
    raw = ps_get_str("realtime_vad_silence_ms", env="DASHSCOPE_REALTIME_VAD_SILENCE_MS", default="400")
    try:
        return max(200, min(6000, int(raw or "400")))
    except Exception:
        return 400


def get_realtime_timeout_seconds() -> float:
    raw = ps_get_str(
        "realtime_timeout_seconds",
        env=["DASHSCOPE_REALTIME_TIMEOUT_SECONDS", "DASHSCOPE_TIMEOUT_SECONDS", "OPENAI_HTTP_TIMEOUT"],
        default="30",
    )
    try:
        return min(60.0, max(1.0, float(raw or "30")))
    except Exception:
        return 30.0


def get_realtime_close_timeout_seconds() -> float:
    raw = ps_get_str("realtime_close_timeout_seconds", env="DASHSCOPE_REALTIME_CLOSE_TIMEOUT_SECONDS", default="0.2")
    try:
        return min(5.0, max(0.0, float(raw or "0.2")))
    except Exception:
        return 0.2


def should_use_realtime() -> bool:
    if ps_get_str("realtime", env="DASHSCOPE_REALTIME", default=None) is not None:
        return ps_get_bool("realtime", env="DASHSCOPE_REALTIME", default=False)
    return False


def get_language() -> str | None:
    return clean_str(ps_get_str("language", env=["DASHSCOPE_LANGUAGE", "OPENAI_TRANSCRIBE_LANGUAGE"], default=None))


def get_result_format() -> str | None:
    return clean_str(ps_get_str("result_format", env="DASHSCOPE_RESULT_FORMAT", default=None))


def get_response_format() -> str | None:
    return clean_str(ps_get_str("response_format", env="DASHSCOPE_RESPONSE_FORMAT", default=None))


def get_context_text() -> str | None:
    def truncate_context_text(s: str) -> str:
        try:
            max_chars = int(os.getenv("DASHSCOPE_CONTEXT_MAX_CHARS", "12000"))
        except Exception:
            max_chars = 12000
        if max_chars <= 0 or len(s) <= max_chars:
            return s
        return s[:max_chars]

    context = clean_str(os.getenv("DASHSCOPE_CONTEXT"))
    if context:
        return truncate_context_text(context)

    prompt = clean_str(ps_get_prompt())
    if prompt:
        return truncate_context_text(prompt)
    return None


def get_incremental_results_enabled() -> bool:
    if ps_get_str("stream", env="DASHSCOPE_STREAM", default=None) is not None:
        return ps_get_bool("stream", env="DASHSCOPE_STREAM", default=False)
    return ps_get_bool("stream", env="OPENAI_TRANSCRIBE_STREAM", default=False)


def get_stream_enabled() -> bool:
    """Backward-compatible alias for incremental transcript responses."""
    return get_incremental_results_enabled()


def get_enable_itn() -> bool:
    return True


def get_enable_lid() -> bool:
    return ps_get_bool("enable_lid", env="DASHSCOPE_ENABLE_LID", default=False)


def build_asr_options() -> Dict[str, Any]:
    asr_options: Dict[str, Any] = {}
    language = get_language()
    if language:
        asr_options["language"] = language
    if get_enable_lid():
        asr_options["enable_lid"] = True
    if get_enable_itn():
        asr_options["enable_itn"] = True
    return asr_options


def build_messages(audio_payload: str, audio_format: str | None = None) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    context = get_context_text()
    if context:
        messages.append({"role": "system", "content": [{"text": context}]})
    if audio_format is None:
        audio_content: Any = audio_payload
    else:
        audio_content = {"format": audio_format, "data": audio_payload}
    messages.append({"role": "user", "content": [{"audio": audio_content}]})
    return messages


def guess_audio_format(mime: str) -> str:
    mime = (mime or "").split(";")[0].strip().lower()
    if "/" in mime:
        subtype = mime.split("/", 1)[1]
    else:
        subtype = mime
    mapping = {
        "mpeg": "mp3",
        "x-m4a": "m4a",
        "x-wav": "wav",
        "wave": "wav",
        "ogg": "ogg",
        "opus": "opus",
        "webm": "webm",
        "aiff": "aiff",
        "x-aiff": "aiff",
    }
    return mapping.get(subtype, subtype or "wav")


def ext_for_mime(mime: str) -> str:
    fmt = guess_audio_format(mime)
    return fmt if fmt else "wav"


def build_request_body(audio_payload: str, audio_format: str) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": get_model(),
        "messages": build_messages(audio_payload, audio_format),
    }

    body["result_format"] = get_result_format() or "message"

    response_format = get_response_format()
    if response_format:
        body["response_format"] = response_format

    asr_options = build_asr_options()
    if asr_options:
        body["asr_options"] = asr_options

    global _STREAM_WARNED
    if get_incremental_results_enabled():
        if not _STREAM_WARNED:
            try:
                console.print(
                    "qwen-audio-legacy incremental results are not supported "
                    "for file upload; falling back to final-only results",
                    style="bright_yellow",
                )
            except Exception:
                pass
            _STREAM_WARNED = True
        body["stream"] = False
    return body


def build_headers() -> dict:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {get_api_key()}",
        "Content-Type": "application/json",
    }
    if not should_reuse_http_client():
        headers["Connection"] = "close"
    return headers


def build_limits() -> httpx.Limits:
    keepalive_expiry = float(os.getenv("DASHSCOPE_KEEPALIVE_EXPIRY", "90"))
    return httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=keepalive_expiry)
