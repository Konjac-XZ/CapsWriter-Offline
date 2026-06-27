"""DashScope Qwen ASR HTTP integration."""
import atexit
import base64
import io
import random
import time
from typing import Any, Dict, Tuple

import httpx

from src.infra.cosmic import console
from src.transcribe.dashscope import settings
from src.transcribe.dashscope.response_parser import (
    extract_transcript,
    payload_preview,
    sdk_response_preview,
)
from src.transcribe.dashscope.sdk_transport import send_with_sdk
from src.transcribe.dashscope.settings import (
    build_headers,
    build_limits,
    build_messages,
    build_request_body,
    candidate_endpoints,
    bool_from_env,
    clean_str,
    ext_for_mime,
    get_timeout_seconds,
    guess_audio_format,
    should_reuse_http_client,
    should_show_debug_logs,
    should_use_http2,
    use_sdk,
)


_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP2_ENABLED: bool = False
_REQUEST_SEQ: int = 0

# Backward-compatible aliases for existing ad hoc imports/tests.
get_api_base = settings.get_api_base
get_api_key = settings.get_api_key
get_context_text = settings.get_context_text
get_enable_itn = settings.get_enable_itn
get_enable_lid = settings.get_enable_lid
get_language = settings.get_language
get_model = settings.get_model
get_response_format = settings.get_response_format
get_result_format = settings.get_result_format
get_incremental_results_enabled = settings.get_incremental_results_enabled
get_stream_enabled = settings.get_stream_enabled

_bool_from_env = bool_from_env
_build_messages = build_messages
_build_request_body = build_request_body
_candidate_endpoints = candidate_endpoints
_clean_str = clean_str
_ext_for_mime = ext_for_mime
_extract_transcript = extract_transcript
_guess_audio_format = guess_audio_format
_payload_preview = payload_preview
_sdk_response_preview = sdk_response_preview
_use_sdk = use_sdk


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
    audio_format = guess_audio_format(payload_mime)
    request_body = build_request_body(encoded_audio, audio_format)
    endpoints = candidate_endpoints()
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
                continue
        else:
            try:
                payload_data = response.json()
            except Exception:
                payload_data = response.text
            transcript, meta = extract_transcript(payload_data)
            meta["dashscope_endpoint"] = endpoint
            return transcript, response.status_code, t_complete, meta, None
    return "", last_status, t_complete, {"dashscope_error": last_error}, last_error


async def _send_with_sdk(
    payload_buf: io.BytesIO,
    payload_mime: str,
) -> Tuple[str, int, float, Dict[str, Any], str | None]:
    return await send_with_sdk(payload_buf, payload_mime)


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

    prefer_sdk = use_sdk()
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
            if prefer_sdk:
                _log_request_event(
                    request_id,
                    "sdk_begin",
                    attempt=attempt + 1,
                    detail=f"task_id={task_id} mime={payload_mime}",
                )
                text_result, status_code, t_complete, meta, err_text = await send_with_sdk(
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
                if prefer_sdk and isinstance(meta, dict) and meta.get("dashscope_sdk_import_error"):
                    import_error = str(meta.get("dashscope_sdk_import_error"))
                    _log_request_event(
                        request_id,
                        "sdk_unavailable",
                        client=client,
                        attempt=attempt + 1,
                        status=status_code,
                        detail=import_error,
                    )
                    client = await get_http_client()
                    close_after_request = not should_reuse_http_client()
                    text_result, status_code, t_complete, meta, err_text = await _send_once(
                        client, payload_buf, payload_mime, request_id, attempt + 1
                    )
                    meta["dashscope_sdk_import_error"] = import_error
                    meta["dashscope_sdk_fallback"] = "http"
                    transport_meta = {"http2": _HTTP2_ENABLED, **meta}
                if err_text and _should_retry(status_code):
                    raise HTTPError(err_text)
            success_detail = f"text_len={len(text_result)}"
            if isinstance(meta, dict) and meta.get("debug_build"):
                success_detail += f" build={meta.get('debug_build')}"
            if isinstance(meta, dict) and meta.get("raw_status") is not None:
                success_detail += f" raw_status={meta.get('raw_status')}"
            if err_text:
                err_preview = " ".join(str(err_text).split())
                if len(err_preview) > 220:
                    err_preview = err_preview[:220] + "..."
                success_detail += f" err={err_preview}"
            _log_request_event(
                request_id,
                "success",
                client=client,
                endpoint=meta.get("dashscope_endpoint") if isinstance(meta, dict) else None,
                attempt=attempt + 1,
                status=status_code,
                elapsed_ms=(t_complete - t_submit) * 1000,
                detail=success_detail,
            )
            if not text_result and isinstance(meta, dict) and meta.get("dashscope_payload_preview"):
                _log_request_event(
                    request_id,
                    "empty_payload",
                    client=client,
                    endpoint=meta.get("dashscope_endpoint"),
                    attempt=attempt + 1,
                    status=status_code,
                    detail=str(meta.get("dashscope_payload_preview")),
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
            if not prefer_sdk and close_after_request and client is not None:
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
