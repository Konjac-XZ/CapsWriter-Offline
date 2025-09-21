import atexit
import io
import json
import os
import random
import time
from typing import Tuple

import httpx

from util.client_cosmic import Cosmic, console


# 全局可复用 HTTP 客户端，启用 keep-alive/可选 HTTP/2，减少重复握手
_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP2_ENABLED: bool = False
_CLIENT_GEN: int = 0


def get_api_base() -> str:
    base_url = os.getenv("OPENAI_BASE_URL")
    if base_url is None:
        return ""
    return base_url.rstrip("/")


def get_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Fail fast: require the API key to be provided via environment variable
        raise RuntimeError("OPENAI_API_KEY environment variable is required but not set")
    return api_key


def get_model() -> str:
    return os.getenv("TRANSCRIBE_MODEL", "gpt-4o-transcribe")


def get_prompt() -> str:
    prompt = os.getenv("TRANSCRIBE_PROMPT")
    return prompt if prompt is not None else ""


def get_temperature() -> float | None:
    val = os.getenv("TRANSCRIBE_TEMPERATURE")
    if val is None or val.strip() == "":
        return None
    try:
        return float(val)
    except Exception:
        return None


def get_language() -> str:
    return os.getenv("OPENAI_TRANSCRIBE_LANGUAGE", "zh")


def is_streaming_enabled() -> bool:
    return os.getenv("OPENAI_TRANSCRIBE_STREAM", "1").strip() not in ("0", "false", "False")


def build_limits() -> httpx.Limits:
    keepalive_expiry = float(os.getenv("OPENAI_KEEPALIVE_EXPIRY", "90"))
    return httpx.Limits(
        max_keepalive_connections=5,
        max_connections=10,
        keepalive_expiry=keepalive_expiry,
    )


def build_headers() -> dict:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {get_api_key()}",
    }


def _log_persistent_established():
    api_base = get_api_base()
    console.print(
        f"持久连接已建立"
    )

def _log_streaming_status():
    status = "开" if is_streaming_enabled() else "关"
    console.print(f"流式转录：{status}")

def _log_persistent_closed(reason: str):
    console.print(
        f"持久连接已关闭"
    )


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
    if _HTTP_CLIENT is not None:
        return _HTTP_CLIENT
    headers = build_headers()
    http2 = os.getenv("OPENAI_HTTP2", "1").strip() not in ("0", "false", "False")
    _HTTP2_ENABLED = http2
    limits = build_limits()
    # Allow overriding request timeout via env for special operations (e.g., availability tests)
    try:
        timeout_s = float(os.getenv("OPENAI_HTTP_TIMEOUT", "120"))
    except Exception:
        timeout_s = 120.0
    _HTTP_CLIENT = httpx.AsyncClient(
        timeout=httpx.Timeout(timeout_s), headers=headers, http2=http2, limits=limits
    )
    _CLIENT_GEN += 1
    _log_persistent_established()
    _log_streaming_status()
    return _HTTP_CLIENT


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


async def emit_partial_update(task_id: str, new_text: str, time_start: float, record_stop: float, t_submit: float):
    """Emit a partial transcription update to the outbound queue."""
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
            "stream": True,
        }
    )


async def sse_transcribe(
    client: httpx.AsyncClient,
    url: str,
    data_form: dict,
    files: dict,
    task_id: str,
    time_start: float,
    record_stop: float,
) -> Tuple[str, int, float]:
    """Perform streaming transcription and emit partial updates.

    Returns (final_text, status_code, t_complete).
    """
    t_submit = time.time()
    current_text = ""
    last_emit = 0.0
    status_code = 0
    async with client.stream("POST", url, data=data_form, files=files) as resp:
        status_code = resp.status_code
        if status_code >= 400:
            body = await resp.aread()
            try:
                err_text = body.decode("utf-8", errors="ignore")
            except Exception:
                err_text = str(body)
            raise httpx.HTTPStatusError("非成功状态码", request=resp.request, response=resp)
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
                current_text += s
                new_text = current_text

            now = time.time()
            if new_text is not None and (now - last_emit >= 0.05) and len(new_text) > 0:
                last_emit = now
                await emit_partial_update(task_id, new_text, time_start, record_stop, t_submit)

    t_complete = time.time()
    return current_text, status_code, t_complete


async def nonstream_transcribe(
    client: httpx.AsyncClient,
    url: str,
    data_form: dict,
    files: dict,
) -> tuple[str, int, float, str | None]:
    """Perform non-streaming transcription. Returns (text, status_code, t_complete, err_text)."""
    resp = await client.post(url, data=data_form, files=files)
    t_complete = time.time()
    status_code = resp.status_code
    if resp.status_code >= 500 or resp.status_code in (408, 429):
        return "", status_code, t_complete, resp.text
    if resp.status_code >= 400:
        console.print(f"服务响应错误：{resp.status_code} {resp.text}", style="bright_red")
        return "", status_code, t_complete, None
    text_result = resp.text
    if len(text_result) >= 2 and text_result.startswith("\"") and text_result.endswith("\""):
        text_result = text_result[1:-1]
    return text_result, status_code, t_complete, None


async def transcribe_with_retries(
    payload_buf: io.BytesIO,
    payload_mime: str,
    data_form_base: dict,
    url: str,
    enable_stream_pref: bool,
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

    from httpx import ReadTimeout, ConnectTimeout, ConnectError, RemoteProtocolError, HTTPError

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

        # Use streaming on the first attempt if enabled; also allow the first retry to try streaming once more
        attempt_stream = enable_stream_pref if attempt == 0 else (enable_stream_pref and (attempt == 1))
        data_form = dict(data_form_base)
        # Provider requires the 'stream' option to be sent as a string; always include it explicitly
        data_form["stream"] = "true" if attempt_stream else "false"
        files = {"file": (fname, payload_buf, payload_mime)}

        err_text = None
        try:
            t_submit = time.time()
            if attempt_stream:
                text_result, status_code, t_complete = await sse_transcribe(
                    client, url, data_form, files, task_id, time_start, record_stop
                )
            else:
                text_result, status_code, t_complete, err_text = await nonstream_transcribe(
                    client, url, data_form, files
                )
                if status_code >= 500 or status_code in (408, 429):
                    raise HTTPError("服务暂时不可用")
            break
        except (ReadTimeout, ConnectTimeout, ConnectError, RemoteProtocolError, HTTPError, OSError) as e:
            t_complete = time.time()
            msg = err_text or str(e)
            console.print(
                f"网络异常（第 {attempt + 1}/{max_retries} 次）：{msg} | http2={_HTTP2_ENABLED} | stream={attempt_stream}",
                style="bright_yellow",
            )
            try:
                await close_http_client(reason=f"error:{e.__class__.__name__}")
            except Exception:
                pass
            if attempt + 1 >= max_retries:
                console.print("已达到最大重试次数，返回当前结果（可能为空）", style="bright_red")
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0.0, 0.1)
            import asyncio

            await asyncio.sleep(delay)

    return text_result, status_code, t_submit, t_complete, _HTTP2_ENABLED


def http2_enabled() -> bool:
    return _HTTP2_ENABLED
