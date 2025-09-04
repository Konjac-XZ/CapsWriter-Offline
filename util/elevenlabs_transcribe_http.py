import atexit
import io
import json
import os
import random
import time
from typing import Tuple

import httpx

from util.client_cosmic import console


_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP2_ENABLED: bool = False


def get_api_base() -> str:
    return os.getenv("ELEVENLABS_BASE_URL", "https://api.elevenlabs.io").rstrip("/")


def get_api_key() -> str:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY environment variable is required for provider=elevenlabs")
    return api_key


def get_model() -> str:
    # Allow override; default to scribe_v1 per docs
    model = os.getenv("ELEVENLABS_STT_MODEL")
    if model and model.strip():
        return model.strip()
    # Fallback: if TRANSCRIBE_MODEL looks like an ElevenLabs STT model, use it; else default
    fallback = os.getenv("TRANSCRIBE_MODEL", "").strip()
    if fallback.lower().startswith("scribe"):
        return fallback
    return "scribe_v1"


def get_language_code() -> str | None:
    # Prefer ELEVENLABS_LANGUAGE_CODE, else reuse OPENAI_TRANSCRIBE_LANGUAGE
    lang = os.getenv("ELEVENLABS_LANGUAGE_CODE")
    if lang and lang.strip():
        return lang.strip()
    lang = os.getenv("OPENAI_TRANSCRIBE_LANGUAGE")
    if lang and lang.strip():
        return lang.strip()
    return None


def get_diarize() -> bool:
    return os.getenv("ELEVENLABS_DIARIZE", "0").strip() not in ("0", "false", "False")


def get_tag_audio_events() -> bool:
    return os.getenv("ELEVENLABS_TAG_AUDIO_EVENTS", "1").strip() not in ("0", "false", "False")


def get_num_speakers() -> int | None:
    raw = os.getenv("ELEVENLABS_NUM_SPEAKERS")
    if not raw:
        return None
    try:
        v = int(raw)
        if v >= 1:
            return v
    except Exception:
        pass
    return None


def get_temperature() -> float | None:
    # Keep provider-agnostic env support
    raw = os.getenv("TRANSCRIBE_TEMPERATURE")
    if raw is None or raw.strip() == "":
        return None
    try:
        return float(raw)
    except Exception:
        return None


def build_limits() -> httpx.Limits:
    keepalive_expiry = float(os.getenv("ELEVENLABS_KEEPALIVE_EXPIRY", "90"))
    return httpx.Limits(
        max_keepalive_connections=5,
        max_connections=10,
        keepalive_expiry=keepalive_expiry,
    )


def build_headers() -> dict:
    return {
        "Accept": "application/json",
        "xi-api-key": get_api_key(),
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
    http2 = os.getenv("ELEVENLABS_HTTP2", "1").strip() not in ("0", "false", "False")
    _HTTP2_ENABLED = http2
    _HTTP_CLIENT = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0),
        headers=build_headers(),
        http2=http2,
        limits=build_limits(),
    )
    try:
        console.print("持久连接已建立 (ElevenLabs)")
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
            console.print("持久连接已关闭 (ElevenLabs)")
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


async def _do_transcribe(
    client: httpx.AsyncClient,
    url: str,
    form: dict,
    files: dict,
) -> tuple[str, int, float]:
    t_submit = time.time()
    resp = await client.post(url, data=form, files=files)
    t_complete = time.time()
    status = resp.status_code
    if status >= 400:
        # Try to extract error
        try:
            err = resp.text
        except Exception:
            err = ""
        try:
            console.print(f"ElevenLabs 响应错误：{status} {err}", style="bright_red")
        except Exception:
            pass
        return "", status, t_complete

    # Parse JSON and normalize to simple text
    text = ""
    try:
        obj = resp.json()
        if isinstance(obj, dict):
            if "text" in obj and isinstance(obj["text"], str):
                text = obj["text"]
            elif "transcripts" in obj and isinstance(obj["transcripts"], (list, tuple)):
                parts = []
                for item in obj["transcripts"]:
                    if isinstance(item, dict) and isinstance(item.get("text"), str):
                        parts.append(item["text"])
                text = "\n".join(p for p in parts if p)
            else:
                # Fallback: stringify whole payload
                text = json.dumps(obj, ensure_ascii=False)
        else:
            text = resp.text
    except Exception:
        text = resp.text

    return text, status, t_complete


async def transcribe_with_retries(
    payload_buf: io.BytesIO,
    payload_mime: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
) -> Tuple[str, int, float, float, bool]:
    """Retry wrapper for ElevenLabs synchronous STT.

    Returns (text_result, status_code, t_submit, t_complete, http2_enabled).
    """
    # Build form
    form: dict = {
        "model_id": get_model(),
        # Tagging audio events on by default, can be toggled
        "tag_audio_events": json.dumps(get_tag_audio_events()),
        "diarize": json.dumps(get_diarize()),
    }
    lang = get_language_code()
    if lang:
        form["language_code"] = lang
    temp = get_temperature()
    if temp is not None:
        form["temperature"] = str(float(temp))
    ns = get_num_speakers()
    if ns is not None:
        form["num_speakers"] = str(int(ns))

    # File field
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
                await close_http_client()
            except Exception:
                pass

        client = await get_http_client()

        files = {"file": (fname, payload_buf, payload_mime)}

        try:
            t_submit = time.time()
            text_result, status_code, t_complete = await _do_transcribe(
                client, f"{get_api_base()}/v1/speech-to-text", form, files
            )
            # Retry on transient server/networky status codes
            if status_code >= 500 or status_code in (408, 429):
                raise HTTPError("服务暂时不可用")
            break
        except (ReadTimeout, ConnectTimeout, ConnectError, RemoteProtocolError, HTTPError, OSError) as e:
            t_complete = time.time()
            try:
                console.print(
                    f"网络异常（第 {attempt + 1}/{max_retries} 次）：{e} | http2={_HTTP2_ENABLED} (ElevenLabs)",
                    style="bright_yellow",
                )
            except Exception:
                pass
            try:
                await close_http_client()
            except Exception:
                pass
            if attempt + 1 >= max_retries:
                try:
                    console.print("已达到最大重试次数，返回当前结果（可能为空）", style="bright_red")
                except Exception:
                    pass
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0.0, 0.1)
            import asyncio

            await asyncio.sleep(delay)

    return text_result, status_code, t_submit, t_complete, _HTTP2_ENABLED


def http2_enabled() -> bool:
    return _HTTP2_ENABLED
