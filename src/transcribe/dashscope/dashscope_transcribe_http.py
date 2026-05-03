"""DashScope Qwen ASR HTTP integration."""
import atexit
import base64
import io
import json
import os
import random
import time
import tempfile
from typing import Any, Dict, List, Tuple

import httpx

from src.infra.cosmic import console
from src.provider.provider_settings import (
    get_str as ps_get_str,
    get_bool as ps_get_bool,
    get_prompt as ps_get_prompt,
)

try:
    from src.provider.provider_config import provider_manager
except Exception:  # pragma: no cover
    provider_manager = None  # type: ignore


_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP2_ENABLED: bool = False
_STREAM_WARNED: bool = False
_REQUEST_SEQ: int = 0

 



def _clean_str(value: str | None) -> str | None:
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def _bool_from_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no"}


def _use_sdk() -> bool:
    # Prefer SDK by default if available
    return _bool_from_env("DASHSCOPE_USE_SDK", True)


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


def _next_request_id() -> str:
    global _REQUEST_SEQ
    _REQUEST_SEQ += 1
    return f"dash-{_REQUEST_SEQ:05d}"


def _client_label(client: httpx.AsyncClient | None) -> str:
    if client is None:
        return "none"
    if _HTTP_CLIENT is client:
        return f"shared#{id(client)}"
    return f"oneshot#{id(client)}"


def _log_request_event(
    request_id: str,
    event: str,
    *,
    client: httpx.AsyncClient | None = None,
    endpoint: str | None = None,
    attempt: int | None = None,
    status: int | None = None,
    elapsed_ms: float | None = None,
    detail: str | None = None,
) -> None:
    if not should_show_debug_logs():
        return

    parts = [f"[dashscope:{request_id}]", event]
    if attempt is not None:
        parts.append(f"attempt={attempt}")
    if client is not None:
        parts.append(f"client={_client_label(client)}")
    parts.append(f"reuse={should_reuse_http_client()}")
    parts.append(f"http2={_HTTP2_ENABLED}")
    if status is not None:
        parts.append(f"status={status}")
    if elapsed_ms is not None:
        parts.append(f"elapsed_ms={elapsed_ms:.1f}")
    if endpoint:
        parts.append(f"endpoint={endpoint}")
    if detail:
        parts.append(f"detail={detail}")
    console.print(" ".join(parts), style="bright_black")


def get_api_base() -> str:
    base = _clean_str(ps_get_str("base_url", env="DASHSCOPE_BASE_URL", default=None))
    if not base:
        base = "https://dashscope.aliyuncs.com"
    return base.rstrip("/")


def get_api_key() -> str:
    key = _clean_str(ps_get_str("api_key", env="DASHSCOPE_API_KEY", default=None))
    if not key:
        raise RuntimeError("DASHSCOPE_API_KEY environment variable is required for provider=dashscope")
    return key


def get_timeout_seconds() -> float:
    raw = ps_get_str("timeout_seconds", env=["DASHSCOPE_TIMEOUT_SECONDS", "OPENAI_HTTP_TIMEOUT"], default="120")
    try:
        val = max(1.0, float(raw or "120"))
        return val
    except Exception:
        return 120.0


def _candidate_endpoints() -> List[str]:
    base = get_api_base()
    override = _clean_str(os.getenv("DASHSCOPE_ENDPOINT"))
    path_override = _clean_str(os.getenv("DASHSCOPE_ENDPOINT_PATH"))
    candidates: List[str] = []

    def _add_endpoint(url: str) -> None:
        if url not in candidates:
            candidates.append(url)

    if override:
        _add_endpoint(override)
        return candidates

    if path_override:
        if path_override.startswith("http"):
            _add_endpoint(path_override)
        else:
            if not path_override.startswith("/"):
                path_override = "/" + path_override
            _add_endpoint(f"{base}{path_override}")

    # Default fallbacks cover current DashScope ASR routes
    _add_endpoint(f"{base}/api/v1/multimodal_conversation")
    _add_endpoint(f"{base}/api/v1/audio/recognitions")
    _add_endpoint(f"{base}/api/v1/services/audio/asr/transcriptions")
    return candidates


def get_model() -> str:
    variant = _clean_str(ps_get_str("model_variant", env="DASHSCOPE_MODEL_VARIANT", default=None))
    if variant:
        return variant
    model = _clean_str(ps_get_str("model", env="DASHSCOPE_MODEL", default=None))
    if model:
        return model
    fallback = _clean_str(os.getenv("TRANSCRIBE_MODEL"))
    if fallback:
        return fallback
    return "qwen3-asr-flash"


def get_language() -> str | None:
    return _clean_str(ps_get_str("language", env=["DASHSCOPE_LANGUAGE", "OPENAI_TRANSCRIBE_LANGUAGE"], default=None))


def get_result_format() -> str | None:
    return _clean_str(ps_get_str("result_format", env="DASHSCOPE_RESULT_FORMAT", default=None))


def get_response_format() -> str | None:
    return _clean_str(ps_get_str("response_format", env="DASHSCOPE_RESPONSE_FORMAT", default=None))


def get_context_text() -> str | None:
    """Return contextual text (aka "prompt") to bias ASR.

    Priority:
    1) DASHSCOPE_CONTEXT env var (raw override)
    2) Provider/YAML prompt preset (via ps_get_prompt)

    The returned text will be truncated to a safe, configurable length to
    avoid exceeding model-side limits (DashScope doc mentions ~10k tokens).
    We cap by characters as an approximation.
    """

    def _truncate_context_text(s: str) -> str:
        # Approximate guard against very large contexts; configurable via env
        # Example: set DASHSCOPE_CONTEXT_MAX_CHARS=20000 to raise the cap
        try:
            max_chars = int(os.getenv("DASHSCOPE_CONTEXT_MAX_CHARS", "12000"))
        except Exception:
            max_chars = 12000
        if max_chars <= 0:
            return s
        if len(s) <= max_chars:
            return s
        return s[:max_chars]

    # 1) explicit env override
    context = _clean_str(os.getenv("DASHSCOPE_CONTEXT"))
    if context:
        return _truncate_context_text(context)

    # 2) YAML/provider prompt preset resolved via DRY utility
    prompt = _clean_str(ps_get_prompt())
    if prompt:
        return _truncate_context_text(prompt)
    return None


def get_stream_enabled() -> bool:
    # YAML -> env(DASHSCOPE_STREAM) -> env(OPENAI_TRANSCRIBE_STREAM) fallback
    if ps_get_str("stream", env="DASHSCOPE_STREAM", default=None) is not None:
        return ps_get_bool("stream", env="DASHSCOPE_STREAM", default=False)
    return ps_get_bool("stream", env="OPENAI_TRANSCRIBE_STREAM", default=False)


def get_enable_itn() -> bool:
    return True  # ITN enabled by default


def get_enable_lid() -> bool:
    return ps_get_bool("enable_lid", env="DASHSCOPE_ENABLE_LID", default=False)


def _build_messages(audio_payload: str, audio_format: str) -> List[Dict[str, Any]]:
    messages: List[Dict[str, Any]] = []
    context = get_context_text()
    if context:
        messages.append({"role": "system", "content": [{"text": context}]})
    messages.append(
        {
            "role": "user",
            "content": [
                {
                    "audio": {
                        "format": audio_format,
                        "data": audio_payload,
                    }
                }
            ],
        }
    )
    return messages


def _guess_audio_format(mime: str) -> str:
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


def _ext_for_mime(mime: str) -> str:
    fmt = _guess_audio_format(mime)
    return fmt if fmt else "wav"


def _extract_text_from_content(content: Any) -> List[str]:
    parts: List[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if "text" in item and isinstance(item["text"], str):
                    parts.append(item["text"])
                elif "content" in item:
                    parts.extend(_extract_text_from_content(item["content"]))
            elif isinstance(item, str):
                parts.append(item)
    elif isinstance(content, dict):
        parts.extend(_extract_text_from_content(content.get("content")))
        if "text" in content and isinstance(content["text"], str):
            parts.append(content["text"])
    elif isinstance(content, str):
        parts.append(content)
    return parts


def _normalize_text(s: str) -> str:
    # strip common wrappers and collapse whitespace
    if not isinstance(s, str):
        return ""
    s = s.strip()
    # remove wrapping quotes if present
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        s = s[1:-1]
    # collapse internal whitespace
    s = " ".join(s.split())
    return s


def _extract_transcript(payload: Any) -> Tuple[str, Dict[str, Any]]:
    request_id = None
    message = None
    # If payload is a JSON string, attempt to parse it first
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            return _extract_transcript(parsed)
        except Exception:
            # treat as plain text
            text = _normalize_text(payload)
            return text, {}
    if isinstance(payload, dict):
        request_id = payload.get("request_id") or payload.get("id")
        message = payload.get("message")
        output = payload.get("output") or payload.get("result") or payload.get("data")
        parts: List[str] = []
        if isinstance(output, dict):
            results = output.get("results") or output.get("choices")
            if isinstance(results, list):
                for item in results:
                    if isinstance(item, dict):
                        parts.extend(_extract_text_from_content(item.get("content")))
                        if "text" in item and isinstance(item["text"], str):
                            parts.append(item["text"])
                        if item.get("message") and isinstance(item["message"], dict):
                            parts.extend(_extract_text_from_content(item["message"].get("content")))
        elif isinstance(output, list):
            for item in output:
                parts.extend(_extract_text_from_content(item))
        if not parts and "text" in payload and isinstance(payload["text"], str):
            parts = [payload["text"]]
        text = "\n".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
        if text:
            text = _normalize_text(text)
    else:
        text = ""

    if not text and payload is not None:
        try:
            text = json.dumps(payload, ensure_ascii=False)
        except Exception:
            text = str(payload)

    meta = {"dashscope_request_id": request_id}
    if message:
        meta["dashscope_message"] = message
    return text, meta


def _build_request_body(audio_payload: str, audio_format: str) -> Dict[str, Any]:
    body: Dict[str, Any] = {
        "model": get_model(),
        "messages": _build_messages(audio_payload, audio_format),
    }

    result_format = get_result_format() or "message"
    body["result_format"] = result_format

    response_format = get_response_format()
    if response_format:
        body["response_format"] = response_format

    language = get_language()
    asr_options: Dict[str, Any] = {}
    if language:
        asr_options["language"] = language
    if get_enable_lid():
        asr_options["enable_lid"] = True
    if get_enable_itn():
        asr_options["enable_itn"] = True
    if asr_options:
        body["asr_options"] = asr_options

    global _STREAM_WARNED
    if get_stream_enabled():
        if not _STREAM_WARNED:
            try:
                console.print("DashScope streaming not yet supported; falling back to non-streaming", style="bright_yellow")
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
    limits = httpx.Limits(max_keepalive_connections=5, max_connections=10, keepalive_expiry=keepalive_expiry)
    return limits


async def close_http_client(reason: str = "manual") -> None:
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        return
    try:
        if should_show_debug_logs():
            console.print(f"DashScope persistent session closed: {reason}")
    except Exception:
        pass
    try:
        await _HTTP_CLIENT.aclose()
    except Exception:
        pass
    _HTTP_CLIENT = None


async def get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT, _HTTP2_ENABLED
    reuse_client = should_reuse_http_client()
    if reuse_client and _HTTP_CLIENT is not None:
        return _HTTP_CLIENT
    http2 = should_use_http2()
    _HTTP2_ENABLED = http2
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(get_timeout_seconds()),
        headers=build_headers(),
        http2=http2,
        limits=build_limits(),
    )
    if reuse_client:
        _HTTP_CLIENT = client
        try:
            if should_show_debug_logs():
                console.print(f"DashScope persistent session ready; HTTP/2={'ON' if http2 else 'OFF'}")
        except Exception:
            pass
    else:
        try:
            if should_show_debug_logs():
                console.print(f"DashScope one-shot session mode; HTTP/2={'ON' if http2 else 'OFF'}", style="bright_yellow")
        except Exception:
            pass
    return client


def _atexit_close_client() -> None:
    client = globals().get("_HTTP_CLIENT")
    if client is None:
        return
    try:
        loop = None
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except Exception:
            loop = None
        try:
            if should_show_debug_logs():
                console.print("DashScope persistent session closed: atexit")
        except Exception:
            pass
        if loop and loop.is_running():
            try:
                loop.create_task(client.aclose())
            except Exception:
                pass
        elif loop and not loop.is_closed():
            try:
                loop.run_until_complete(client.aclose())
            except Exception:
                pass
    except Exception:
        pass


def _decode_audio_payload(payload_buf: io.BytesIO) -> Tuple[str, Dict[str, Any]]:
    try:
        payload_buf.seek(0)
    except Exception:
        pass
    audio_bytes = payload_buf.read()
    encoded = base64.b64encode(audio_bytes).decode("ascii")
    meta = {
        "bytes": len(audio_bytes),
        "preview": audio_bytes[:16].hex(),
    }
    return encoded, meta


def _read_audio_bytes(payload_buf: io.BytesIO) -> bytes:
    try:
        payload_buf.seek(0)
    except Exception:
        pass
    return payload_buf.read()


def _should_retry(status_code: int) -> bool:
    return status_code >= 500 or status_code in (408, 409, 429, 499)


async def _send_once(
    client: httpx.AsyncClient,
    payload_buf: io.BytesIO,
    payload_mime: str,
    request_id: str,
    attempt: int,
) -> Tuple[str, int, float, Dict[str, Any], str | None]:
    encoded_audio, _ = _decode_audio_payload(payload_buf)
    audio_format = _guess_audio_format(payload_mime)
    request_body = _build_request_body(encoded_audio, audio_format)
    endpoints = _candidate_endpoints()
    last_status = 0
    last_error = None
    t_complete = time.time()

    for endpoint in endpoints:
        t0 = time.time()
        _log_request_event(request_id, "post_begin", client=client, endpoint=endpoint, attempt=attempt)
        try:
            response = await client.post(endpoint, json=request_body)
        except Exception as exc:
            _log_request_event(
                request_id,
                "post_exception",
                client=client,
                endpoint=endpoint,
                attempt=attempt,
                elapsed_ms=(time.time() - t0) * 1000,
                detail=f"{exc.__class__.__name__}: {exc}",
            )
            last_error = str(exc)
            continue
        t_complete = time.time()
        last_status = response.status_code
        _log_request_event(
            request_id,
            "post_done",
            client=client,
            endpoint=endpoint,
            attempt=attempt,
            status=response.status_code,
            elapsed_ms=(t_complete - t0) * 1000,
        )
        if response.status_code >= 400:
            try:
                err_text = response.text
            except Exception:
                err_text = ""
            last_error = err_text or f"HTTP {response.status_code}"
            if response.status_code in (404, 405):
                # try next candidate endpoint
                continue
            payload_data = None
        else:
            try:
                payload_data = response.json()
            except Exception:
                payload_data = response.text
            transcript, meta = _extract_transcript(payload_data)
            meta["dashscope_endpoint"] = endpoint
            return transcript, response.status_code, t_complete, meta, None
    # No successful endpoint
    return "", last_status, t_complete, {"dashscope_error": last_error}, last_error


async def _send_with_sdk(
    payload_buf: io.BytesIO,
    payload_mime: str,
) -> Tuple[str, int, float, Dict[str, Any], str | None]:
    """Use official dashscope SDK for transcription. Creates a temp local file
    and passes absolute path as required by the SDK for local audio.
    """
    import asyncio
    t_complete = time.time()
    meta: Dict[str, Any] = {"via": "dashscope-sdk"}
    try:
        import dashscope
    except Exception as exc:  # SDK not available
        return "", 0, t_complete, meta, str(exc)

    audio_bytes = _read_audio_bytes(payload_buf)
    ext = _ext_for_mime(payload_mime)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as f:
            f.write(audio_bytes)
            tmp_path = f.name

        # Build messages per docs: local audio as absolute path string
        context = get_context_text()
        messages: List[Dict[str, Any]] = []
        if context:
            messages.append({"role": "system", "content": [{"text": context}]})
        messages.append({"role": "user", "content": [{"audio": tmp_path}]})
        model = get_model()

        asr_options: Dict[str, Any] = {}
        language = get_language()
        if language:
            asr_options["language"] = language
        if get_enable_lid():
            asr_options["enable_lid"] = True
        if get_enable_itn():
            asr_options["enable_itn"] = True


        # Call SDK in a thread to avoid blocking event loop
        def _call_sdk() -> Any:
            return dashscope.MultiModalConversation.call(
                api_key=get_api_key(),
                model=model,
                messages=messages,
                result_format="message",
                asr_options=asr_options,
                # stream not used here (non-streaming)
            )

        response = await asyncio.to_thread(_call_sdk)
        t_complete = time.time()

        # Convert response to a dict-ish structure for a unified parser
        payload_data: Any
        try:
            if hasattr(response, "to_dict"):
                payload_data = response.to_dict()  # type: ignore[attr-defined]
            elif isinstance(response, dict):
                payload_data = response
            else:
                payload_data = json.loads(str(response))
        except Exception:
            payload_data = str(response)

        transcript, extra = _extract_transcript(payload_data)
        meta.update(extra)
        meta["dashscope_sdk"] = True
        # Best-effort status code
        status = 200
        if isinstance(payload_data, dict):
            status = int(payload_data.get("status_code") or payload_data.get("code") or 200)
        return transcript, status, t_complete, meta, None if transcript else None
    except Exception as exc:
        t_complete = time.time()
        err = str(exc)
        return "", 400, t_complete, meta, err
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
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
) -> Tuple[str, int, float, float, Dict[str, Any]]:
    from httpx import ConnectError, ConnectTimeout, HTTPError, ReadTimeout, RemoteProtocolError

    text_result = ""
    status_code = 0
    t_submit = time.time()
    t_complete = t_submit
    transport_meta: Dict[str, Any] = {"http2": _HTTP2_ENABLED}

    use_sdk = _use_sdk()
    for attempt in range(max_retries):
        if attempt > 0:
            try:
                await close_http_client(reason=f"retry:{attempt}")
            except Exception:
                pass
        client = None
        close_after_request = False
        request_id = _next_request_id()
        try:
            t_submit = time.time()
            if use_sdk:
                _log_request_event(
                    request_id,
                    "sdk_begin",
                    attempt=attempt + 1,
                    detail=f"task_id={task_id} mime={payload_mime}",
                )
                text_result, status_code, t_complete, meta, err_text = await _send_with_sdk(
                    payload_buf, payload_mime
                )
            else:
                client = await get_http_client()
                close_after_request = not should_reuse_http_client()
                _log_request_event(
                    request_id,
                    "dispatch",
                    client=client,
                    attempt=attempt + 1,
                    detail=f"task_id={task_id} mime={payload_mime}",
                )
                text_result, status_code, t_complete, meta, err_text = await _send_once(
                    client, payload_buf, payload_mime, request_id, attempt + 1
                )
            transport_meta = {"http2": _HTTP2_ENABLED, **meta}
            if err_text and _should_retry(status_code):
                raise HTTPError(err_text)
            _log_request_event(
                request_id,
                "success",
                client=client,
                endpoint=meta.get("dashscope_endpoint") if isinstance(meta, dict) else None,
                attempt=attempt + 1,
                status=status_code,
                elapsed_ms=(t_complete - t_submit) * 1000,
                detail=f"text_len={len(text_result)}",
            )
            break
        except (ReadTimeout, ConnectTimeout, ConnectError, RemoteProtocolError, HTTPError, OSError) as exc:
            t_complete = time.time()
            _log_request_event(
                request_id,
                "exception",
                client=client,
                attempt=attempt + 1,
                status=status_code or None,
                elapsed_ms=(t_complete - t_submit) * 1000,
                detail=f"{exc.__class__.__name__}: {exc}",
            )
            try:
                console.print(
                    f"DashScope network issue (attempt {attempt + 1}/{max_retries}): {exc}",
                    style="bright_yellow",
                )
            except Exception:
                pass
        finally:
            if not use_sdk and close_after_request and client is not None:
                _log_request_event(request_id, "client_close", client=client, attempt=attempt + 1)
                try:
                    await client.aclose()
                except Exception:
                    pass
        if attempt + 1 >= max_retries:
            try:
                console.print("DashScope reached max retry count", style="bright_red")
            except Exception:
                pass
            break
        delay = base_delay * (2 ** attempt) + random.uniform(0.0, 0.1)
        import asyncio

        await asyncio.sleep(delay)

    if not text_result:
        try:
            level = "bright_yellow" if status_code and status_code < 500 else "bright_red"
            console.print(
                f"DashScope returned empty transcript (status={status_code}).",
                style=level,
            )
        except Exception:
            pass
    return text_result, status_code, t_submit, t_complete, transport_meta


def http2_enabled() -> bool:
    return _HTTP2_ENABLED


atexit.register(_atexit_close_client)
