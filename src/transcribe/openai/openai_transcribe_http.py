import atexit
import io
import json
import os
import random
import time
from typing import Tuple

import httpx
from src.infra.response_parse import (
    extract_text_from_body,
    extract_json_from_labeled_line,
)

from src.infra.cosmic import Cosmic, console
from src.provider.provider_settings import (
    get_str as ps_get_str,
    get_prompt as ps_get_prompt,
    get_bool as ps_get_bool,
    get_float as ps_get_float,
)


# 全局可复用 HTTP 客户端，启用 keep-alive/可选 HTTP/2，减少重复握手
_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP2_ENABLED: bool = False
_CLIENT_GEN: int = 0
_REQUEST_SEQ: int = 0


def get_api_base() -> str:
    base_url = ps_get_str("base_url", env="OPENAI_BASE_URL", default="") or ""
    return base_url.rstrip("/")


def _is_dashscope_compatible_base() -> bool:
    base = get_api_base().lower()
    return "dashscope.aliyuncs.com" in base or "aliyuncs.com/compatible-mode" in base


def get_api_key() -> str:
    api_key = ps_get_str("api_key", env="OPENAI_API_KEY")
    if not api_key:
        # Fail fast: require the API key to be provided via environment variable
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is required but not set"
        )
    return api_key


def get_model() -> str:
    return (
        ps_get_str("model", env="TRANSCRIBE_MODEL", default="gpt-4o-transcribe")
        or "gpt-4o-transcribe"
    )


def get_prompt() -> str:
    return ps_get_prompt()


def get_temperature() -> float | None:
    val = ps_get_float("temperature", env="TRANSCRIBE_TEMPERATURE", default=None)
    return None if val is None else float(val)


def get_language() -> str:
    return (
        ps_get_str("language", env="OPENAI_TRANSCRIBE_LANGUAGE", default="zh") or "zh"
    )


def is_incremental_results_enabled() -> bool:
    return ps_get_bool("stream", env="OPENAI_TRANSCRIBE_STREAM", default=True)


def is_streaming_enabled() -> bool:
    """Backward-compatible alias for incremental transcript responses."""
    return is_incremental_results_enabled()


def should_reuse_http_client() -> bool:
    if (
        ps_get_str("reuse_http_client", env="OPENAI_REUSE_HTTP_CLIENT", default=None)
        is not None
    ):
        return ps_get_bool(
            "reuse_http_client", env="OPENAI_REUSE_HTTP_CLIENT", default=True
        )
    return True


def should_use_http2() -> bool:
    if ps_get_str("http2", env="OPENAI_HTTP2", default=None) is not None:
        return ps_get_bool("http2", env="OPENAI_HTTP2", default=True)
    return True


def build_limits() -> httpx.Limits:
    keepalive_expiry = float(os.getenv("OPENAI_KEEPALIVE_EXPIRY", "90"))
    return httpx.Limits(
        max_keepalive_connections=5,
        max_connections=10,
        keepalive_expiry=keepalive_expiry,
    )


def build_headers() -> dict:
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {get_api_key()}",
    }
    if not should_reuse_http_client():
        headers["Connection"] = "close"
    return headers


def _log_persistent_established():
    console.print("持久连接已建立")


def _log_incremental_results_status():
    status = "开" if is_incremental_results_enabled() else "关"
    console.print(f"增量转录结果：{status}")


def _log_persistent_closed(reason: str):
    console.print("持久连接已关闭")


def _next_request_id() -> str:
    global _REQUEST_SEQ
    _REQUEST_SEQ += 1
    return f"oai-{_REQUEST_SEQ:05d}"


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
    url: str | None = None,
    attempt: int | None = None,
    incremental_results: bool | None = None,
    status: int | None = None,
    elapsed_ms: float | None = None,
    detail: str | None = None,
) -> None:
    parts = [f"[openai:{request_id}]", event]
    if attempt is not None:
        parts.append(f"attempt={attempt}")
    if client is not None:
        parts.append(f"client={_client_label(client)}")
    parts.append(f"reuse={should_reuse_http_client()}")
    parts.append(f"http2={_HTTP2_ENABLED}")
    if incremental_results is not None:
        parts.append(f"incremental_results={incremental_results}")
    if status is not None:
        parts.append(f"status={status}")
    if elapsed_ms is not None:
        parts.append(f"elapsed_ms={elapsed_ms:.1f}")
    if url:
        parts.append(f"url={url}")
    if detail:
        parts.append(f"detail={detail}")
    console.print(" ".join(parts), style="bright_black")


async def close_http_client(reason: str = "manual"):
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        return
    try:
        _log_persistent_closed(reason)
    except Exception:
        pass
    try:
        await _HTTP_CLIENT.aclose()
    except Exception:
        pass
    _HTTP_CLIENT = None


async def get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT, _HTTP2_ENABLED, _CLIENT_GEN
    reuse_client = should_reuse_http_client()
    if reuse_client and _HTTP_CLIENT is not None:
        return _HTTP_CLIENT
    headers = build_headers()
    http2 = should_use_http2()
    _HTTP2_ENABLED = http2
    limits = build_limits()
    # Allow overriding request timeout via env, capped to keep stuck requests bounded.
    try:
        timeout_s = min(30.0, max(1.0, float(os.getenv("OPENAI_HTTP_TIMEOUT", "30"))))
    except Exception:
        timeout_s = 30.0
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s), headers=headers, http2=http2, limits=limits
    )
    _CLIENT_GEN += 1
    if reuse_client:
        _HTTP_CLIENT = client
        _log_persistent_established()
    else:
        console.print("当前端点已禁用持久连接复用", style="bright_yellow")
    _log_incremental_results_status()
    return client


def _atexit_close_client():
    # Best-effort close of the persistent AsyncClient on process exit.
    # This tries to avoid complaints if the loop is already closed.
    try:
        client = globals().get("_HTTP_CLIENT")
        if client is None:
            return
        loop = None
        try:
            import asyncio

            loop = asyncio.get_event_loop()
        except Exception:
            loop = None
        # Log before attempting close
        try:
            _log_persistent_closed("process-exit")
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


# Register best-effort cleanup
atexit.register(_atexit_close_client)


async def emit_transcript_delta(
    task_id: str, new_text: str, time_start: float, record_stop: float, t_submit: float
):
    """Emit an incremental transcript update to the outbound queue."""
    await Cosmic.queue_out.put(
        {
            "task_id": task_id,
            "is_final": False,
            "text": new_text,
            "time_start": time_start,
            "time_stop": record_stop,
            "time_submit": t_submit,
            "time_complete": time.time(),
            "source": "mic",
            "is_transcript_delta": True,
            # Backward compatibility for older result consumers.
            "stream": True,
        }
    )


async def sse_transcribe_incremental_results(
    client: httpx.AsyncClient,
    url: str,
    data_form: dict,
    files: dict,
    task_id: str,
    time_start: float,
    record_stop: float,
    request_id: str,
    attempt: int,
) -> Tuple[str, int, float]:
    """Request incremental transcript responses and emit transcript deltas.

    Returns (final_text, status_code, t_complete).
    """
    t_submit = time.time()
    current_text = ""
    last_emit = 0.0
    status_code = 0
    _log_request_event(
        request_id,
        "sse_open",
        client=client,
        url=url,
        attempt=attempt,
        incremental_results=True,
    )
    async with client.stream("POST", url, data=data_form, files=files) as resp:
        status_code = resp.status_code
        _log_request_event(
            request_id,
            "sse_headers",
            client=client,
            url=url,
            attempt=attempt,
            incremental_results=True,
            status=status_code,
        )
        if status_code >= 400:
            raise httpx.HTTPStatusError(
                "非成功状态码", request=resp.request, response=resp
            )
        async for line in resp.aiter_lines():
            if not line:
                continue
            s = line.strip()
            if s.startswith(":"):
                continue
            if s.startswith("data:"):
                s = s[5:].strip()
            if s in ("[DONE]", "DONE"):
                break
            new_text = None
            try:
                # First try strict JSON
                obj = json.loads(s)
                if isinstance(obj, dict):
                    if "delta" in obj and isinstance(obj["delta"], str):
                        current_text += obj["delta"]
                        new_text = current_text
                    elif "text" in obj and isinstance(obj["text"], str):
                        current_text = obj["text"]
                        new_text = current_text
                    elif "choices" in obj:
                        try:
                            delta = obj["choices"][0]["delta"].get("content")
                            if isinstance(delta, str):
                                current_text += delta
                                new_text = current_text
                        except Exception:
                            pass
                elif isinstance(obj, str):
                    current_text = obj
                    new_text = current_text
            except Exception:
                # Try to handle lines like: "识别结果：{...json...}"
                try:
                    obj2 = extract_json_from_labeled_line(s)
                except Exception:
                    obj2 = None
                if isinstance(obj2, dict):
                    if "delta" in obj2 and isinstance(obj2["delta"], str):
                        current_text += obj2["delta"]
                        new_text = current_text
                    elif "text" in obj2 and isinstance(obj2["text"], str):
                        current_text = obj2["text"]
                        new_text = current_text
                    else:
                        # Fallback to best-effort body parsing
                        txt = extract_text_from_body(s)
                        if isinstance(txt, str) and txt:
                            current_text = txt
                            new_text = current_text
                        else:
                            current_text += s
                            new_text = current_text
                else:
                    # Fallback to best-effort body parsing
                    txt = extract_text_from_body(s)
                    if isinstance(txt, str) and txt:
                        current_text = txt
                        new_text = current_text
                    else:
                        current_text += s
                        new_text = current_text

            now = time.time()
            if new_text is not None and (now - last_emit >= 0.05) and len(new_text) > 0:
                last_emit = now
                await emit_transcript_delta(
                    task_id, new_text, time_start, record_stop, t_submit
                )

    t_complete = time.time()
    _log_request_event(
        request_id,
        "sse_done",
        client=client,
        url=url,
        attempt=attempt,
        incremental_results=True,
        status=status_code,
        elapsed_ms=(t_complete - t_submit) * 1000,
        detail=f"text_len={len(current_text)}",
    )
    return current_text, status_code, t_complete


async def nonstream_transcribe(
    client: httpx.AsyncClient,
    url: str,
    data_form: dict,
    files: dict,
    request_id: str,
    attempt: int,
) -> tuple[str, int, float, str | None]:
    """Perform non-streaming transcription. Returns (text, status_code, t_complete, err_text)."""
    t0 = time.time()
    _log_request_event(
        request_id,
        "post_begin",
        client=client,
        url=url,
        attempt=attempt,
        incremental_results=False,
    )
    resp = await client.post(url, data=data_form, files=files)
    t_complete = time.time()
    status_code = resp.status_code
    _log_request_event(
        request_id,
        "post_done",
        client=client,
        url=url,
        attempt=attempt,
        incremental_results=False,
        status=status_code,
        elapsed_ms=(t_complete - t0) * 1000,
    )
    if resp.status_code >= 500 or resp.status_code in (408, 429):
        return "", status_code, t_complete, resp.text
    if resp.status_code >= 400:
        console.print(
            f"OpenAI 服务响应错误：{resp.status_code} {resp.text}", style="bright_red"
        )
        return "", status_code, t_complete, None
    # Parse plain-text, JSON, or labeled JSON bodies to extract transcript text
    body = resp.text
    parsed = None
    try:
        parsed = extract_text_from_body(body)
    except Exception:
        parsed = None
    text_result = parsed if isinstance(parsed, str) and parsed.strip() != "" else body
    # Some providers return quoted plain text (e.g., "你好")
    if (
        len(text_result) >= 2
        and text_result.startswith('"')
        and text_result.endswith('"')
    ):
        text_result = text_result[1:-1]
    return text_result, status_code, t_complete, None


async def transcribe_with_retries(
    payload_buf: io.BytesIO,
    payload_mime: str,
    data_form_base: dict,
    url: str,
    enable_incremental_results: bool,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
) -> tuple[str, int, float, float, bool]:
    """Retry wrapper that recreates the persistent client before each retry.

    Returns (text_result, status_code, t_submit, t_complete, http2_enabled).
    """
    fname = "mic.mp3" if payload_mime == "audio/mpeg" else "mic.wav"
    text_result = ""
    status_code = 0
    t_submit = time.time()
    t_complete = t_submit

    from httpx import (
        ReadTimeout,
        ConnectTimeout,
        ConnectError,
        RemoteProtocolError,
        HTTPError,
    )

    for attempt in range(max_retries):
        try:
            payload_buf.seek(0)
        except Exception:
            pass

        if attempt > 0:
            try:
                await close_http_client(reason=f"retry-recreate:{attempt}")
            except Exception:
                pass

        client = await get_http_client()
        close_after_request = not should_reuse_http_client()
        request_id = _next_request_id()

        # Use incremental transcript responses on the first attempt if enabled;
        # also allow the first retry to try them once more.
        attempt_incremental_results = (
            enable_incremental_results
            if attempt == 0
            else (enable_incremental_results and (attempt == 1))
        )
        data_form = dict(data_form_base)
        # Provider requires the 'stream' option to be sent as a string; always include it explicitly
        data_form["stream"] = "true" if attempt_incremental_results else "false"
        files = {"file": (fname, payload_buf, payload_mime)}

        err_text = None
        try:
            t_submit = time.time()
            _log_request_event(
                request_id,
                "dispatch",
                client=client,
                url=url,
                attempt=attempt + 1,
                incremental_results=attempt_incremental_results,
                detail=f"task_id={task_id} mime={payload_mime}",
            )
            if attempt_incremental_results:
                (
                    text_result,
                    status_code,
                    t_complete,
                ) = await sse_transcribe_incremental_results(
                    client,
                    url,
                    data_form,
                    files,
                    task_id,
                    time_start,
                    record_stop,
                    request_id,
                    attempt + 1,
                )
            else:
                (
                    text_result,
                    status_code,
                    t_complete,
                    err_text,
                ) = await nonstream_transcribe(
                    client, url, data_form, files, request_id, attempt + 1
                )
                if status_code >= 500 or status_code in (408, 429):
                    raise HTTPError("服务暂时不可用")
            _log_request_event(
                request_id,
                "success",
                client=client,
                url=url,
                attempt=attempt + 1,
                incremental_results=attempt_incremental_results,
                status=status_code,
                elapsed_ms=(t_complete - t_submit) * 1000,
                detail=f"text_len={len(text_result)}",
            )
            break
        except (
            ReadTimeout,
            ConnectTimeout,
            ConnectError,
            RemoteProtocolError,
            HTTPError,
            OSError,
        ) as e:
            t_complete = time.time()
            msg = err_text or str(e)
            _log_request_event(
                request_id,
                "exception",
                client=client,
                url=url,
                attempt=attempt + 1,
                incremental_results=attempt_incremental_results,
                status=status_code or None,
                elapsed_ms=(t_complete - t_submit) * 1000,
                detail=f"{e.__class__.__name__}: {msg}",
            )
            console.print(
                f"网络异常（第 {attempt + 1}/{max_retries} 次）：{msg} | http2={_HTTP2_ENABLED} | incremental_results={attempt_incremental_results}",
                style="bright_yellow",
            )
            try:
                await close_http_client(reason=f"error:{e.__class__.__name__}")
            except Exception:
                pass
            if attempt + 1 >= max_retries:
                console.print(
                    "已达到最大重试次数，返回当前结果（可能为空）", style="bright_red"
                )
                break
            delay = base_delay * (2**attempt) + random.uniform(0.0, 0.1)
            import asyncio

            await asyncio.sleep(delay)
        finally:
            if close_after_request:
                _log_request_event(
                    request_id,
                    "client_close",
                    client=client,
                    url=url,
                    attempt=attempt + 1,
                    incremental_results=attempt_incremental_results,
                )
                try:
                    await client.aclose()
                except Exception:
                    pass

    return text_result, status_code, t_submit, t_complete, _HTTP2_ENABLED


def http2_enabled() -> bool:
    return _HTTP2_ENABLED
