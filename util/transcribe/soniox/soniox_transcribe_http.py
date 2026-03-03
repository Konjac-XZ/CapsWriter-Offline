import atexit
import io
import json
import os
import random
import time
from typing import Tuple

import httpx

from util.client_cosmic import console
from util.provider_settings import (
    get_str as ps_get_str,
    get_bool as ps_get_bool,
    get_prompt as ps_get_prompt,
)


_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP2_ENABLED: bool = False


def get_api_base() -> str:
    base = ps_get_str("base_url", env="SONIOX_BASE_URL", default="https://api.soniox.com") or "https://api.soniox.com"
    return base.rstrip("/")


def get_api_key() -> str:
    key = ps_get_str("api_key", env=["SONIOX_API_KEY", "SONIOX_TEMP_API_KEY"], default=None)
    if not key:
        raise RuntimeError("SONIOX_API_KEY (or SONIOX_TEMP_API_KEY) is required for provider=soniox")
    return key


def get_model() -> str:
    m = ps_get_str("model", env=["SONIOX_MODEL", "TRANSCRIBE_MODEL"], default=None)
    if m:
        return m
    # Async REST model default
    return "stt-async-preview"


def get_language_hints() -> list[str] | None:
    raw = ps_get_str("language_hints", env="SONIOX_LANGUAGE_HINTS", default=None)
    if raw and raw.strip():
        parts = [p.strip() for p in raw.split(",")]
        hints = [p for p in parts if p]
        return hints or None
    one = os.getenv("OPENAI_TRANSCRIBE_LANGUAGE")
    if one and one.strip():
        return [one.strip()]
    return None


def get_context() -> str | None:
    ctx = ps_get_prompt()
    ctx = ctx.strip() if isinstance(ctx, str) else ""
    return ctx or None


def get_enable_diarization() -> bool:
    return ps_get_bool("enable_diarization", env="SONIOX_ENABLE_DIARIZATION", default=False)


def get_enable_language_identification() -> bool:
    return ps_get_bool("enable_language_identification", env="SONIOX_ENABLE_LANGUAGE_IDENTIFICATION", default=False)


def build_limits() -> httpx.Limits:
    keepalive_expiry = float(os.getenv("SONIOX_KEEPALIVE_EXPIRY", "90"))
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


async def close_http_client():
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        return
    try:
        await _HTTP_CLIENT.aclose()
    except Exception:
        pass
    _HTTP_CLIENT = None


async def get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT, _HTTP2_ENABLED
    if _HTTP_CLIENT is not None:
        return _HTTP_CLIENT
    http2 = os.getenv("SONIOX_HTTP2", "1").strip() not in ("0", "false", "False")
    _HTTP2_ENABLED = http2
    _HTTP_CLIENT = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0),
        headers=build_headers(),
        http2=http2,
        limits=build_limits(),
    )
    try:
        console.print("持久连接已建立 (Soniox REST)")
    except Exception:
        pass
    return _HTTP_CLIENT


def _atexit_close_client():
    try:
        client = globals().get("_HTTP_CLIENT")
        if client is None:
            return
        try:
            import asyncio

            loop = asyncio.get_event_loop()
        except Exception:
            loop = None
        try:
            console.print("持久连接已关闭 (Soniox REST)")
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


atexit.register(_atexit_close_client)


def _guess_filename(payload_mime: str) -> str:
    if not payload_mime:
        return "mic.wav"
    m = payload_mime.lower()
    if "mpeg" in m or "mp3" in m:
        return "mic.mp3"
    if "wav" in m or "wave" in m:
        return "mic.wav"
    if "ogg" in m:
        return "mic.ogg"
    return "mic.wav"


async def _upload_audio_file(client: httpx.AsyncClient, payload_buf: io.BytesIO, payload_mime: str) -> tuple[str | None, int]:
    """Upload audio bytes to Soniox Files API and return (file_id, status).

    Primary endpoint: POST /v1/files (multipart with field 'file').
    Fallbacks: /v1/files/upload_file, /v1/files/upload-file
    """
    base = get_api_base()
    endpoints = [
        f"{base}/v1/files",
        f"{base}/v1/files/upload_file",
        f"{base}/v1/files/upload-file",
    ]
    try:
        try:
            payload_buf.seek(0)
        except Exception:
            pass
        try:
            data = payload_buf.getvalue()
        except Exception:
            data = payload_buf.read()
        if not data:
            return None, 400
        fname = _guess_filename(payload_mime)
        files = {"file": (fname, data, payload_mime or "application/octet-stream")}
        last_status = 0
        last_text = ""
        for url in endpoints:
            resp = await client.post(url, files=files)
            status = resp.status_code
            if status == 404:
                last_status, last_text = status, resp.text
                continue
            if status >= 400:
                try:
                    console.print(f"Soniox 上传文件失败：{status} {resp.text}", style="bright_red")
                except Exception:
                    pass
                return None, status
            try:
                obj = resp.json()
            except Exception:
                obj = {}
            file_id = obj.get("id") or obj.get("file_id")
            if not file_id:
                return None, 500
            return file_id, status
        # If all endpoints 404
        if last_status == 404:
            try:
                console.print(f"Soniox 上传端点未找到：{last_text}", style="bright_red")
            except Exception:
                pass
            return None, 404
        return None, 503
    except Exception as e:
        try:
            console.print(f"Soniox 上传异常：{e}", style="bright_yellow")
        except Exception:
            pass
        return None, 503


async def _create_transcription(client: httpx.AsyncClient, file_id: str, task_id: str | None) -> tuple[str | None, int, str | None]:
    url = f"{get_api_base()}/v1/transcriptions"
    body: dict = {
        "model": get_model(),
    "file_id": file_id,
    "client_reference_id": task_id,
        "enable_speaker_diarization": get_enable_diarization(),
        "enable_language_identification": get_enable_language_identification(),
    }
    hints = get_language_hints()
    if hints:
        body["language_hints"] = hints
    ctx = get_context()
    if ctx:
        body["context"] = ctx

    resp = await client.post(url, json=body)
    status = resp.status_code
    if status >= 400:
        try:
            console.print(f"Soniox REST 创建任务失败：{status} {resp.text}", style="bright_red")
        except Exception:
            pass
        return None, status, None
    try:
        obj = resp.json()
    except Exception:
        obj = {}
    return obj.get("id"), status, obj.get("status")


async def _get_transcription_status(client: httpx.AsyncClient, t_id: str) -> tuple[str | None, int, str | None]:
    url = f"{get_api_base()}/v1/transcriptions/{t_id}"
    resp = await client.get(url)
    status = resp.status_code
    if status >= 400:
        return None, status, None
    try:
        obj = resp.json()
    except Exception:
        obj = {}
    return obj.get("status"), status, obj.get("error_message")


async def _get_transcript_text(client: httpx.AsyncClient, t_id: str) -> tuple[str, int]:
    url = f"{get_api_base()}/v1/transcriptions/{t_id}/transcript"
    resp = await client.get(url)
    status = resp.status_code
    if status >= 400:
        return "", status
    try:
        obj = resp.json()
        if isinstance(obj, dict) and isinstance(obj.get("text"), str):
            return obj["text"], status
        return resp.text, status
    except Exception:
        return resp.text, status


async def transcribe_with_retries(
    payload_buf: io.BytesIO,
    payload_mime: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
) -> Tuple[str, int, float, float, bool]:
    """Soniox REST transcription flow with upload->create->poll->fetch transcript.

    Returns (text_result, status_code, t_submit, t_complete, http2_enabled).
    """
    text_result = ""
    status_code = 0
    t_submit = time.time()
    t_complete = t_submit

    from httpx import ReadTimeout, ConnectTimeout, ConnectError, RemoteProtocolError, HTTPError

    for attempt in range(max_retries):
        # Reset buffer
        try:
            payload_buf.seek(0)
        except Exception:
            pass

        if attempt > 0:
            try:
                await close_http_client()
            except Exception:
                pass

        client = await get_http_client()

        try:
            # 1) Upload to Soniox Files service
            file_id, up_status = await _upload_audio_file(client, payload_buf, payload_mime)
            if not file_id:
                status_code = up_status
                if status_code >= 500 or status_code in (408, 429):
                    raise HTTPError("服务暂时不可用")
                break

            # 2) Create transcription
            t_submit = time.time()
            t_id, create_status, init_state = await _create_transcription(client, file_id, task_id)
            if not t_id:
                status_code = create_status
                if status_code >= 500 or status_code in (408, 429):
                    raise HTTPError("服务暂时不可用")
                break

            # 3) Poll until completed or error
            poll_start = time.time()
            status_code = 201
            polling_timeout = float(os.getenv("SONIOX_POLL_TIMEOUT", "25"))
            poll_interval = 0.25
            while True:
                state, st, err_msg = await _get_transcription_status(client, t_id)
                # Update status_code to last HTTP if non-2xx
                if st and st >= 400:
                    status_code = st
                    break
                if state == "completed":
                    # 4) Fetch transcript
                    text_result, status_code = await _get_transcript_text(client, t_id)
                    break
                if state == "error":
                    status_code = 500
                    try:
                        console.print(f"Soniox 任务失败：{err_msg}", style="bright_red")
                    except Exception:
                        pass
                    break
                if time.time() - poll_start > polling_timeout:
                    status_code = 504
                    break
                # backoff a bit up to ~1s
                await _sleep(poll_interval)
                poll_interval = min(1.0, poll_interval * 1.2)

            t_complete = time.time()
            # Successful fetch
            if status_code == 200 and text_result is not None:
                break

            # If we reach here and error is transient, retry
            if status_code >= 500 or status_code in (408, 429, 504):
                raise HTTPError("服务暂时不可用")
            else:
                break

        except (ReadTimeout, ConnectTimeout, ConnectError, RemoteProtocolError, HTTPError, OSError) as e:
            t_complete = time.time()
            try:
                console.print(
                    f"Soniox REST 网络/服务异常（第 {attempt + 1}/{max_retries} 次）：{e} | http2={_HTTP2_ENABLED}",
                    style="bright_yellow",
                )
            except Exception:
                pass

        # Retry backoff
        if attempt + 1 >= max_retries:
            try:
                console.print("已达到最大重试次数，返回当前结果（可能为空）", style="bright_red")
            except Exception:
                pass
            break
        delay = base_delay * (2 ** attempt) + random.uniform(0.0, 0.1)
        await _sleep(delay)

    return text_result, status_code, t_submit, t_complete, _HTTP2_ENABLED


async def _sleep(sec: float):
    import asyncio

    await asyncio.sleep(sec)


def http2_enabled() -> bool:
    return _HTTP2_ENABLED
