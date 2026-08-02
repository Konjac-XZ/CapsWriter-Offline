from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from typing import Any, Optional, Tuple

import httpx

from src.infra.cosmic import console
from src.infra.user_lexicon import load_words as load_user_lexicon_words
from src.provider.provider_settings import (
    get_bool as ps_get_bool,
    get_int as ps_get_int,
    get_str as ps_get_str,
    get_value as ps_get_value,
)


_HTTP_CLIENT: Optional[httpx.AsyncClient] = None
_GENERATION_PATH = "/api/v1/services/aigc/multimodal-generation/generation"
_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com"
_MAX_ENCODED_AUDIO_BYTES = 10 * 1024 * 1024
_MAX_HOTWORDS = 2000
_MAX_SUPER_HOTWORDS = 50
_LAST_VOCABULARY_WARNING: Optional[tuple[str, ...]] = None


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def get_api_key() -> str:
    return ps_get_str("api_key", env="DASHSCOPE_API_KEY", default="") or ""


def get_model() -> str:
    return (
        ps_get_str(
            "model",
            env="QWEN_AUDIO_3_MODEL",
            default="qwen-audio-3.0-asr-flash",
        )
        or "qwen-audio-3.0-asr-flash"
    )


def get_timeout_seconds() -> float:
    raw = ps_get_str(
        "timeout_seconds",
        env=["QWEN_AUDIO_3_TIMEOUT_SECONDS", "DASHSCOPE_TIMEOUT_SECONDS"],
        default="60",
    )
    try:
        return min(300.0, max(1.0, float(raw or "60")))
    except Exception:
        return 60.0


def should_use_http2() -> bool:
    return ps_get_bool("http2", env="QWEN_AUDIO_3_HTTP2", default=True)


def should_show_debug_logs() -> bool:
    return ps_get_bool("debug", env="QWEN_AUDIO_3_DEBUG", default=False)


def should_log_request_payload() -> bool:
    return should_show_debug_logs() and ps_get_bool(
        "log_request_payload",
        env="QWEN_AUDIO_3_LOG_REQUEST_PAYLOAD",
        default=False,
    )


def get_asr_context_settings() -> dict[str, Any]:
    configured = ps_get_value("asr_context", env=None, default=None)
    return configured if isinstance(configured, dict) else {}


def should_use_asr_context() -> bool:
    configured = get_asr_context_settings()
    value = configured.get("enabled", True)
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def should_use_asr_history() -> bool:
    history = get_asr_context_settings().get("history", {})
    if not isinstance(history, dict):
        return True
    return bool(history.get("enabled", True))


def get_asr_history_max_messages() -> int:
    history = get_asr_context_settings().get("history", {})
    raw = history.get("max_messages", 4) if isinstance(history, dict) else 4
    try:
        return max(0, min(5, int(raw)))
    except Exception:
        return 4


def get_asr_history_max_chars() -> int:
    history = get_asr_context_settings().get("history", {})
    raw = (
        history.get("max_chars_per_message", 400) if isinstance(history, dict) else 400
    )
    try:
        return max(1, min(400, int(raw)))
    except Exception:
        return 400


def should_use_asr_textbox() -> bool:
    textbox = get_asr_context_settings().get("textbox", {})
    if not isinstance(textbox, dict):
        return True
    return bool(textbox.get("enabled", True))


def get_asr_textbox_max_chars() -> int:
    textbox = get_asr_context_settings().get("textbox", {})
    raw = textbox.get("max_chars", 400) if isinstance(textbox, dict) else 400
    try:
        return max(1, min(400, int(raw)))
    except Exception:
        return 400


def get_asr_context_capture_timeout_seconds() -> float:
    raw = get_asr_context_settings().get("capture_timeout_ms", 300)
    try:
        return max(0.0, min(2.0, float(raw) / 1000.0))
    except Exception:
        return 0.3


def get_api_url() -> str:
    endpoint = ps_get_str("endpoint", env="QWEN_AUDIO_3_ENDPOINT", default=None)
    if endpoint:
        return endpoint.rstrip("/")

    workspace_id = ps_get_str(
        "workspace_id", env="DASHSCOPE_WORKSPACE_ID", default=None
    )
    if workspace_id:
        region = (
            (
                ps_get_str("region", env="DASHSCOPE_REGION", default="cn-beijing")
                or "cn-beijing"
            )
            .strip()
            .lower()
        )
        region_domains = {
            "cn-beijing": "cn-beijing.maas.aliyuncs.com",
            "beijing": "cn-beijing.maas.aliyuncs.com",
            "ap-southeast-1": "ap-southeast-1.maas.aliyuncs.com",
            "singapore": "ap-southeast-1.maas.aliyuncs.com",
        }
        domain = region_domains.get(region)
        if domain is None:
            raise ValueError(
                "Qwen Audio 3.0 region must be cn-beijing or ap-southeast-1"
            )
        return f"https://{workspace_id}.{domain}{_GENERATION_PATH}"

    base_url = (
        ps_get_str("base_url", env="QWEN_AUDIO_3_BASE_URL", default=_DEFAULT_BASE_URL)
        or _DEFAULT_BASE_URL
    ).rstrip("/")
    if base_url.endswith(_GENERATION_PATH):
        return base_url
    if base_url.endswith("/api/v1"):
        return f"{base_url}/services/aigc/multimodal-generation/generation"
    return f"{base_url}{_GENERATION_PATH}"


def build_headers(api_key: str) -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-SSE": "disable",
    }


def build_limits() -> httpx.Limits:
    return httpx.Limits(max_keepalive_connections=5, max_connections=10)


def get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(
            http2=should_use_http2(),
            timeout=httpx.Timeout(get_timeout_seconds()),
            limits=build_limits(),
        )
    return _HTTP_CLIENT


async def close_http_client() -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is not None:
        await _HTTP_CLIENT.aclose()
        _HTTP_CLIENT = None


def normalize_audio_type(payload_mime: str) -> tuple[str, str]:
    mime = (payload_mime or "").split(";", 1)[0].strip().lower()
    mapping = {
        "audio/mpeg": ("audio/mpeg", "mp3"),
        "audio/mp3": ("audio/mpeg", "mp3"),
        "audio/x-mpeg": ("audio/mpeg", "mp3"),
        "audio/wav": ("audio/wav", "wav"),
        "audio/wave": ("audio/wav", "wav"),
        "audio/x-wav": ("audio/wav", "wav"),
        "audio/opus": ("audio/opus", "opus"),
        "audio/ogg": ("audio/opus", "opus"),
    }
    if mime not in mapping:
        raise ValueError(
            f"Qwen Audio 3.0 does not support payload MIME type: {payload_mime or '<empty>'}"
        )
    return mapping[mime]


def encode_audio(payload_buf: io.BytesIO) -> tuple[str, int]:
    if hasattr(payload_buf, "seek"):
        payload_buf.seek(0)
    raw = payload_buf.read()
    return base64.b64encode(raw).decode("ascii"), len(raw)


def build_audio_data_uri(payload_mime: str, audio_b64: str) -> str:
    canonical_mime, _ = normalize_audio_type(payload_mime)
    return f"data:{canonical_mime};base64,{audio_b64}"


def _parse_language_hints(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            parsed = json.loads(stripped)
        except Exception:
            parsed = [part.strip() for part in stripped.split(",")]
    else:
        parsed = value
    if not isinstance(parsed, (list, tuple)):
        return []
    result: list[str] = []
    for item in parsed:
        language = _clean_str(item).lower()
        if language and language not in result:
            result.append(language)
    return result[:4]


def get_language_hints() -> list[str]:
    configured = ps_get_value("language_hints", env=None, default=None)
    if configured is None:
        legacy = ps_get_str(
            "language",
            env=["QWEN_AUDIO_3_LANGUAGE", "OPENAI_TRANSCRIBE_LANGUAGE"],
            default=None,
        )
        configured = legacy
    return _parse_language_hints(configured)


def _parse_vocabulary(value: Any) -> dict[str, int]:
    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except Exception:
            return {}
    if not isinstance(value, dict):
        return {}
    result: dict[str, int] = {}
    for raw_word, raw_weight in value.items():
        word = _clean_str(raw_word)
        if not word:
            continue
        try:
            weight = int(raw_weight)
        except Exception:
            continue
        if weight == 50 or 1 <= weight <= 5:
            result[word] = weight
    return result


def should_use_user_lexicon() -> bool:
    return ps_get_bool(
        "use_user_lexicon",
        env="QWEN_AUDIO_3_USE_USER_LEXICON",
        default=True,
    )


def get_user_lexicon_weight() -> int:
    configured = ps_get_int(
        "user_lexicon_weight",
        env="QWEN_AUDIO_3_USER_LEXICON_WEIGHT",
        default=4,
    )
    if configured is None or not (1 <= configured <= 5):
        return 4
    return configured


def normalize_hotword_text(value: Any) -> str:
    """Normalize conservative whole-word Markdown wrappers from the GUI lexicon."""
    text = _clean_str(value)
    if (
        len(text) >= 4
        and text.startswith("\\`")
        and (text.endswith("`\\") or text.endswith("\\`"))
    ):
        text = text[2:-2].strip()
    elif len(text) >= 2 and text.startswith("`") and text.endswith("`"):
        text = text[1:-1].strip()
    return text


def validate_hotword_text(text: str) -> str | None:
    """Return an Alibaba hotword-rule violation, or None when valid."""
    if not text:
        return "内容为空"
    if any(ord(char) > 127 for char in text):
        if len(text) > 15:
            return f"包含非 ASCII 字符且长度为 {len(text)}，上限为 15"
        return None
    segments = text.split()
    if len(segments) > 7:
        return f"纯 ASCII 热词包含 {len(segments)} 个空格分段，上限为 7"
    return None


def _raw_provider_vocabulary() -> Any:
    return ps_get_value("vocabulary", env=None, default=None)


def _provider_vocabulary_with_issues() -> tuple[dict[str, int], list[str]]:
    raw = _raw_provider_vocabulary()
    if raw is None or raw == "":
        return {}, []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return {}, ["供应商 vocabulary 不是有效的 JSON 对象"]
    if not isinstance(raw, dict):
        return {}, ["供应商 vocabulary 必须是 YAML/JSON 对象"]

    vocabulary: dict[str, int] = {}
    issues: list[str] = []
    for raw_word, raw_weight in raw.items():
        word = normalize_hotword_text(raw_word)
        if not word:
            issues.append("供应商 vocabulary 中存在空热词")
            continue
        try:
            weight = int(raw_weight)
        except Exception:
            issues.append(f"{word!r} 的权重 {raw_weight!r} 不是整数")
            continue
        if weight != 50 and not 1 <= weight <= 5:
            issues.append(f"{word!r} 的权重 {weight} 无效，只允许 1-5 或 50")
            continue
        violation = validate_hotword_text(word)
        if violation:
            issues.append(f"{word!r}：{violation}")
            continue
        if word in vocabulary:
            issues.append(
                f"供应商 vocabulary 中的 {word!r} 规范化后重复，已使用最后一个权重"
            )
        vocabulary[word] = weight
    return vocabulary, issues


def resolve_vocabulary() -> tuple[dict[str, int], list[str]]:
    """Merge the live GUI lexicon with provider overrides and enforce API limits."""
    issues: list[str] = []
    base_vocabulary: dict[str, int] = {}
    if should_use_user_lexicon():
        default_weight = get_user_lexicon_weight()
        for raw_word in load_user_lexicon_words():
            word = normalize_hotword_text(raw_word)
            violation = validate_hotword_text(word)
            if violation:
                issues.append(f"用户词库 {word!r}：{violation}")
                continue
            if word in base_vocabulary:
                issues.append(f"用户词库 {word!r} 重复，已去重")
                continue
            base_vocabulary[word] = default_weight

    explicit, explicit_issues = _provider_vocabulary_with_issues()
    issues.extend(explicit_issues)

    merged = dict(base_vocabulary)
    super_count = 0
    applied_explicit: list[str] = []
    for word, weight in explicit.items():
        if weight == 50:
            if super_count >= _MAX_SUPER_HOTWORDS:
                issues.append(
                    f"供应商超级热词 {word!r} 超过 {_MAX_SUPER_HOTWORDS} 条上限，已忽略覆盖"
                )
                continue
            super_count += 1
        merged[word] = weight
        applied_explicit.append(word)

    if len(merged) <= _MAX_HOTWORDS:
        return merged, issues

    # Explicit provider entries take priority when the merged list exceeds the API limit.
    limited: dict[str, int] = {}
    for word in applied_explicit:
        if word in merged and word not in limited:
            limited[word] = merged[word]
            if len(limited) >= _MAX_HOTWORDS:
                break
    if len(limited) < _MAX_HOTWORDS:
        for word, weight in merged.items():
            if word in limited:
                continue
            limited[word] = weight
            if len(limited) >= _MAX_HOTWORDS:
                break
    issues.append(
        f"合并后共有 {len(merged)} 条热词，超过 {_MAX_HOTWORDS} 条上限，已截断"
    )
    return limited, issues


def _summarize_vocabulary_issues(issues: list[str]) -> str:
    counts = {
        "去重": sum("重复，已去重" in issue for issue in issues),
        "长度超限": sum(
            "上限为 15" in issue or "上限为 7" in issue for issue in issues
        ),
        "超级热词超限": sum(
            "超级热词" in issue and "上限" in issue for issue in issues
        ),
        "总数截断": sum(
            "合并后共有" in issue and "已截断" in issue for issue in issues
        ),
    }
    summarized = sum(counts.values())
    if summarized < len(issues):
        counts["其他调整"] = len(issues) - summarized
    return "，".join(f"{label} {count} 条" for label, count in counts.items() if count)


def _report_vocabulary_issues(issues: list[str], vocabulary_count: int) -> None:
    global _LAST_VOCABULARY_WARNING
    signature = tuple(issues)
    if not signature:
        _LAST_VOCABULARY_WARNING = None
        return
    if signature == _LAST_VOCABULARY_WARNING:
        return
    _LAST_VOCABULARY_WARNING = signature
    summary = _summarize_vocabulary_issues(issues)
    try:
        console.print(
            f"Qwen Audio 3.0 热词已处理：发送 {vocabulary_count} 条；{summary}。",
            style="bright_yellow",
        )
    except Exception:
        pass


def get_vocabulary() -> dict[str, int]:
    vocabulary, issues = resolve_vocabulary()
    _report_vocabulary_issues(issues, len(vocabulary))
    return vocabulary


def _truncate_context_text(value: Any, max_chars: int) -> str:
    text = str(value or "").replace("\x00", "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _textbox_context_snippet(captured: Any, max_chars: int) -> str:
    text = str(getattr(captured, "text", "") or "").replace("\x00", "")
    if not text.strip():
        return ""
    if len(text) <= max_chars:
        return text.strip()

    caret = getattr(captured, "caret_offset", None)
    try:
        caret_offset = int(caret) if caret is not None else None
    except Exception:
        caret_offset = None
    if caret_offset is None or not 0 <= caret_offset <= len(text):
        return text[-max_chars:].strip()

    before = max_chars // 2
    start = max(0, caret_offset - before)
    end = min(len(text), start + max_chars)
    start = max(0, end - max_chars)
    return text[start:end].strip()


def build_asr_context_messages(request_context: Any) -> list[dict[str, Any]]:
    if request_context is None or not should_use_asr_context():
        return []

    textbox_text = ""
    if should_use_asr_textbox():
        textbox_text = _textbox_context_snippet(
            getattr(request_context, "captured_textbox_context", None),
            get_asr_textbox_max_chars(),
        )

    history_values: list[Any] = []
    if should_use_asr_history():
        raw_history = getattr(request_context, "asr_history", None)
        if raw_history is None:
            raw_history = getattr(request_context, "history", None)
        if isinstance(raw_history, list):
            history_values = raw_history

    max_history = get_asr_history_max_messages()
    if textbox_text:
        max_history = min(max_history, 4)
    history_texts = (
        [
            _truncate_context_text(item, get_asr_history_max_chars())
            for item in history_values[-max_history:]
        ]
        if max_history > 0
        else []
    )
    history_texts = [text for text in history_texts if text]

    context_texts = history_texts
    if textbox_text:
        context_texts.append(textbox_text)
    context_texts = context_texts[-5:]

    return [
        {
            "role": "user",
            "content": [{"type": "input_text", "text": text}],
        }
        for text in context_texts
    ]


def build_request_body(
    payload_mime: str,
    audio_b64: str,
    request_context: Any = None,
) -> dict[str, Any]:
    _, audio_format = normalize_audio_type(payload_mime)
    parameters: dict[str, Any] = {"format": audio_format}

    sample_rate = ps_get_int(
        "request_sample_rate",
        env="QWEN_AUDIO_3_REQUEST_SAMPLE_RATE",
        default=None,
    )
    if sample_rate and sample_rate > 0:
        parameters["sample_rate"] = str(sample_rate)

    language_hints = get_language_hints()
    if language_hints:
        parameters["language_hints"] = language_hints

    vocabulary = get_vocabulary()
    if vocabulary:
        parameters["vocabulary"] = vocabulary
    else:
        vocabulary_id = ps_get_str(
            "vocabulary_id", env="QWEN_AUDIO_3_VOCABULARY_ID", default=None
        )
        if vocabulary_id:
            parameters["vocabulary_id"] = vocabulary_id

    messages = build_asr_context_messages(request_context)
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "type": "input_audio",
                    "input_audio": {
                        "data": build_audio_data_uri(payload_mime, audio_b64),
                    },
                }
            ],
        }
    )

    return {
        "model": get_model(),
        "input": {"messages": messages},
        "parameters": parameters,
    }


def request_body_for_debug(value: Any) -> Any:
    """Return a detached request copy with every audio body removed."""
    if isinstance(value, dict):
        return {
            str(key): request_body_for_debug(child)
            for key, child in value.items()
            if key != "input_audio"
        }
    if isinstance(value, list):
        return [request_body_for_debug(item) for item in value]
    return value


def _log_request_payload(request_body: dict[str, Any]) -> None:
    if not should_log_request_payload():
        return
    try:
        payload_text = json.dumps(
            request_body_for_debug(request_body),
            ensure_ascii=False,
            indent=2,
        )
        console.print(
            f"[qwen-audio][request-payload; audio omitted]\n{payload_text}",
            style="bright_black",
        )
    except Exception:
        pass


def extract_transcript(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    output = payload.get("output")
    if not isinstance(output, dict):
        return ""

    direct_text = output.get("text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()

    nested_output = output.get("output")
    candidates = [nested_output, output]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        nested_text = candidate.get("text")
        if isinstance(nested_text, str) and nested_text.strip():
            return nested_text.strip()
        sentence = candidate.get("sentence")
        if isinstance(sentence, dict):
            sentence_text = sentence.get("text")
            if isinstance(sentence_text, str) and sentence_text.strip():
                return sentence_text.strip()
    return ""


def _response_metadata(
    payload: Any, *, audio_bytes: int, audio_format: str
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "provider": "qwen-audio",
        "audio_bytes": audio_bytes,
        "audio_format": audio_format,
    }
    if not isinstance(payload, dict):
        return meta
    request_id = payload.get("request_id")
    if request_id:
        meta["request_id"] = request_id
    usage = payload.get("usage")
    if isinstance(usage, dict):
        meta["usage"] = usage
    output = payload.get("output")
    if isinstance(output, dict) and isinstance(output.get("sentence"), dict):
        meta["sentence"] = output["sentence"]
    return meta


def _error_text(response: Optional[httpx.Response]) -> str:
    if response is None:
        return "Qwen Audio 3.0 request failed: no response"
    try:
        data = response.json()
        if isinstance(data, dict):
            message = data.get("message")
            if isinstance(message, str) and message.strip():
                return message.strip()
            error = data.get("error")
            if isinstance(error, dict) and isinstance(error.get("message"), str):
                return error["message"].strip()
            code = data.get("code")
            if code:
                return f"{code}: {data}"
    except Exception:
        pass
    return response.text.strip() or f"HTTP {response.status_code}"


def _should_retry(status_code: int) -> bool:
    return status_code in (0, 408, 409, 429, 499, 500, 502, 503, 504)


def _report_error(message: str) -> None:
    try:
        console.print(message, style="bright_red")
    except Exception:
        pass


async def transcribe_with_retries(
    payload_buf: io.BytesIO,
    payload_mime: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
    request_context: Any = None,
) -> Tuple[str, int, float, float, dict[str, Any]]:
    del task_id, time_start, record_stop

    now = time.time()
    api_key = get_api_key()
    if not api_key:
        error = "Qwen Audio 3.0 API key is missing. Set DASHSCOPE_API_KEY."
        _report_error(error)
        return (
            "",
            0,
            now,
            now,
            {"provider": "qwen-audio", "http2": False, "error": error},
        )

    try:
        audio_b64, audio_bytes = encode_audio(payload_buf)
        _, audio_format = normalize_audio_type(payload_mime)
        if audio_bytes <= 0:
            raise ValueError("Qwen Audio 3.0 audio payload is empty")
        encoded_audio_bytes = len(audio_b64.encode("ascii"))
        if encoded_audio_bytes > _MAX_ENCODED_AUDIO_BYTES:
            raise ValueError(
                "Qwen Audio 3.0 Base64 audio exceeds the 10 MB request limit: "
                f"{encoded_audio_bytes} encoded bytes"
            )
        request_body = build_request_body(payload_mime, audio_b64, request_context)
        url = get_api_url()
    except ValueError as exc:
        error = str(exc)
        _report_error(error)
        return (
            "",
            0,
            now,
            time.time(),
            {
                "provider": "qwen-audio",
                "http2": False,
                "error": error,
            },
        )

    _log_request_payload(request_body)
    headers = build_headers(api_key)
    last_text = ""
    last_status = 0
    t_submit = now
    t_complete = now
    meta: dict[str, Any] = {
        "provider": "qwen-audio",
        "audio_bytes": audio_bytes,
        "encoded_audio_bytes": encoded_audio_bytes,
        "audio_format": audio_format,
        "vocabulary_count": len(
            request_body.get("parameters", {}).get("vocabulary", {})
        ),
        "asr_context_messages": max(
            0, len(request_body.get("input", {}).get("messages", [])) - 1
        ),
        "asr_context_chars": sum(
            len(str(message.get("content", [{}])[0].get("text", "")))
            for message in request_body.get("input", {}).get("messages", [])[:-1]
        ),
        "http2": False,
    }

    retry_count = max(1, int(max_retries))
    for attempt in range(retry_count):
        response: Optional[httpx.Response] = None
        t_submit = time.time()
        try:
            response = await get_http_client().post(
                url, headers=headers, json=request_body
            )
            t_complete = time.time()
            last_status = response.status_code
            meta["http2"] = response.http_version == "HTTP/2"
            if 200 <= response.status_code < 300:
                try:
                    payload = response.json()
                except Exception:
                    payload = None
                transcript = extract_transcript(payload)
                meta.update(
                    _response_metadata(
                        payload,
                        audio_bytes=audio_bytes,
                        audio_format=audio_format,
                    )
                )
                if transcript:
                    return transcript, response.status_code, t_submit, t_complete, meta
                last_text = "Qwen Audio 3.0 returned no transcript"
                meta["empty_response"] = True
                meta["error"] = last_text
                _report_error(last_text)
                return "", response.status_code, t_submit, t_complete, meta

            last_text = _error_text(response)
            if not _should_retry(response.status_code):
                meta["error"] = last_text
                _report_error(
                    f"Qwen Audio 3.0 request failed (HTTP {response.status_code}): {last_text}"
                )
                return "", response.status_code, t_submit, t_complete, meta
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
            last_text = f"Qwen Audio 3.0 request error: {exc}"
            try:
                await close_http_client()
            except Exception:
                pass

        if attempt + 1 < retry_count and _should_retry(last_status):
            await asyncio.sleep(base_delay * (2**attempt))
            continue
        meta["error"] = last_text
        _report_error(f"Qwen Audio 3.0 request failed: {last_text}")
        return "", last_status, t_submit, t_complete, meta

    meta["error"] = last_text
    return "", last_status, t_submit, t_complete, meta
