import io
import os
import time
import random
import json
from typing import Tuple

import httpx

from util.client_cosmic import console
try:
    from util.provider_config import provider_manager
except Exception:
    provider_manager = None
from util.openai_transcribe_http import emit_partial_update


def _get_model() -> str:
    """Replicate model identifier. Prefer YAML settings model; fallback to env."""
    if provider_manager is not None:
        try:
            s = provider_manager.get_active_settings()
            m = s.get("model")
            if isinstance(m, str) and m.strip():
                return "openai/" + m.strip()
        except Exception:
            pass
    return "openai/" + os.getenv("TRANSCRIBE_MODEL", "gpt-4o-mini-transcribe")


def _get_input_key() -> str:
    """The input field name for audio on Replicate; default to 'audio_file' for wide compatibility."""
    return os.getenv("TRANSCRIBE_REPLICATE_AUDIO_KEY", "audio_file")


def _ensure_token():
    token = None
    if provider_manager is not None:
        try:
            token = provider_manager.get_active_settings().get("api_token")
        except Exception:
            token = None
    if not token:
        token = os.getenv("REPLICATE_API_TOKEN")
    if not token:
        raise RuntimeError("REPLICATE_API_TOKEN environment variable is required for provider=replicate")


def _replicate_debug() -> bool:
    return os.getenv("REPLICATE_DEBUG", "1").strip() not in ("0", "false", "False")


def _log_send_info(model: str, input_key: str, audio_input, payload_mime: str | None = None):
    try:
        if not _replicate_debug():
            return
        info = {
            "model": model,
            "input_key": input_key,
            "input_type": type(audio_input).__name__,
            "mime": payload_mime,
        }
        # If it's a file object or has a name, try to show name and size
        try:
            name = getattr(audio_input, "name", None)
            if name and os.path.exists(name):
                info["path"] = name
                info["size_bytes"] = os.path.getsize(name)
        except Exception:
            pass
        # If it's a path string, show existence and size
        try:
            if isinstance(audio_input, str):
                info["path"] = audio_input
                if os.path.exists(audio_input):
                    info["size_bytes"] = os.path.getsize(audio_input)
        except Exception:
            pass
        console.print("[Replicate Debug] Sending:", info, style="dim")
    except Exception:
        pass


def _mime_to_suffix(payload_mime: str) -> str:
    """Map common audio MIME types to safe file extensions."""
    if not payload_mime:
        return ".wav"
    m = payload_mime.lower()
    if "mpeg" in m or "mp3" in m:
        return ".mp3"
    if "wav" in m or "wave" in m:
        return ".wav"
    if "ogg" in m:
        return ".ogg"
    if "mpeg" in m:
        return ".mp3"
    # Default to .wav
    return ".wav"


async def _upload_file_to_host(payload_buf: io.BytesIO, payload_mime: str) -> str:
    """Upload audio to a third-party host (default: Litterbox) and return a public URL.

    Env overrides:
      - THIRD_PARTY_UPLOAD_URL (default: https://litterbox.catbox.moe/resources/internals/api.php)
      - THIRD_PARTY_UPLOAD_TIME (default: 1h)
      - THIRD_PARTY_UPLOAD_FIELD (default: fileToUpload)
      - THIRD_PARTY_UPLOAD_REQTYPE (default: fileupload)
    """
    upload_url = os.getenv(
        "THIRD_PARTY_UPLOAD_URL",
        "https://litterbox.catbox.moe/resources/internals/api.php",
    )
    time_param = os.getenv("THIRD_PARTY_UPLOAD_TIME", "1h")
    field_name = os.getenv("THIRD_PARTY_UPLOAD_FIELD", "fileToUpload")
    reqtype = os.getenv("THIRD_PARTY_UPLOAD_REQTYPE", "fileupload")

    suffix = _mime_to_suffix(payload_mime)
    filename = f"audio_file{suffix}"

    try:
        payload_buf.seek(0)
    except Exception:
        pass

    try:
        data = payload_buf.getvalue()
    except Exception:
        data = payload_buf.read()
    if not data:
        raise ValueError("Audio buffer is empty (0 bytes)")

    files = {
        field_name: (filename, data, "application/octet-stream"),
    }
    form = {
        "reqtype": reqtype,
        "time": time_param,
    }

    if _replicate_debug():
        console.print(
            f"[Upload Debug] -> {upload_url} filename={filename} bytes={len(data)} field={field_name} time={time_param}",
            style="dim",
        )

    async with httpx.AsyncClient() as client:
        resp = await client.post(upload_url, data=form, files=files)
        if resp.status_code >= 400:
            raise Exception(f"Third-party upload failed: {resp.status_code} {resp.text}")
        url = resp.text.strip()
        if not url.startswith("http"):
            # Some hosts may return plain path; try to parse JSON or fail loudly
            try:
                obj = resp.json()
                url = obj.get("url") or obj.get("urls", {}).get("get") or url
            except Exception:
                pass
        if _replicate_debug():
            console.print(f"[Upload Debug] <- {url}", style="dim")
        if not url.startswith("http"):
            raise Exception(f"Unexpected upload response: {resp.text}")
        return url


async def _streaming_run(
    audio_url: str,
    language: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    prompt: str | None = None,
    temperature: float | None = None,
) -> Tuple[str, int, float, float]:
    import replicate

    model = _get_model()
    input_key = _get_input_key()
    # Pass the uploaded URL to Replicate
    _log_send_info(model, input_key, audio_url, None)
    inputs = {"language": language, input_key: audio_url}
    if prompt:
        inputs["prompt"] = prompt
    if temperature is not None:
        inputs["temperature"] = float(temperature)

    t_submit = time.time()
    current = ""
    last_emit = 0.0
    try:
        for event in replicate.stream(model, input=inputs):
            # Each event renders to the next chunk of text
            chunk = str(event)
            if not chunk:
                continue
            current += chunk
            now = time.time()
            if now - last_emit >= 0.05:
                last_emit = now
                # Reuse existing partial emitter to keep outbound schema stable
                await emit_partial_update(task_id, current, time_start, record_stop, t_submit)
    except Exception as e:
        console.print(f"Replicate 流式转录异常：{e}", style="bright_yellow")
        # Treat as failure; caller may retry non-streaming
        t_complete = time.time()
        return current, 503, t_submit, t_complete

    t_complete = time.time()
    return current, 200, t_submit, t_complete


async def _nonstream_run(
    audio_url: str,
    language: str,
    prompt: str | None = None,
    temperature: float | None = None,
) -> Tuple[str, int, float, float]:
    import replicate

    model = _get_model()
    input_key = _get_input_key()
    _log_send_info(model, input_key, audio_url, None)
    inputs = {"language": language, input_key: audio_url}
    if prompt:
        inputs["prompt"] = prompt
    if temperature is not None:
        inputs["temperature"] = float(temperature)

    t_submit = time.time()
    try:
        text = replicate.run(model, input=inputs)
        t_complete = time.time()
        # replicate.run may return a list/iterator or dict depending on model; normalize to str
        if isinstance(text, (list, tuple)):
            text = "".join(str(x) for x in text)
        elif not isinstance(text, str):
            text = str(text)
        return text, 200, t_submit, t_complete
    except Exception as e:
        t_complete = time.time()
        console.print(f"Replicate 非流式转录异常：{e}", style="bright_yellow")
        return "", 503, t_submit, t_complete


async def transcribe_with_retries(
    payload_buf: io.BytesIO,
    payload_mime: str,
    language: str,
    enable_stream_pref: bool,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
    prompt: str | None = None,
    temperature: float | None = None,
) -> Tuple[str, int, float, float]:
    """Replicate retry wrapper. Tries streaming first if enabled, then falls back with backoff."""
    _ensure_token()

    # Determine suffix for temp file upload
    suffix = ".mp3" if payload_mime == "audio/mpeg" else ".wav"

    text_result = ""
    status_code = 0
    t_submit = time.time()
    t_complete = t_submit

    # Upload audio once per attempt via third-party host
    for attempt in range(max_retries):
        audio_url = None
        try:
            audio_url = await _upload_file_to_host(payload_buf, payload_mime)
        except Exception as e:
            console.print(f"Replicate 文件上传失败（第 {attempt + 1}/{max_retries} 次）：{e}", style="bright_red")
            if attempt + 1 >= max_retries:
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0.0, 0.1)
            import asyncio

            await asyncio.sleep(delay)
            continue

        try:
            # Pass the uploaded URL to the model
            if enable_stream_pref and attempt == 0:
                text_result, status_code, t_submit, t_complete = await _streaming_run(
                    audio_url, language, task_id, time_start, record_stop, prompt, temperature
                )
                if status_code == 200:
                    break
            # Non-streaming path or retry after streaming failure
            text_result, status_code, t_submit, t_complete = await _nonstream_run(
                audio_url, language, prompt, temperature
            )
            if status_code == 200:
                break
        except Exception as e:
            t_complete = time.time()
            console.print(
                f"Replicate 转录异常（第 {attempt + 1}/{max_retries} 次）：{e}", style="bright_yellow"
            )

        if attempt + 1 >= max_retries:
            break
        delay = base_delay * (2 ** attempt) + random.uniform(0.0, 0.1)
        import asyncio

        await asyncio.sleep(delay)

    return text_result, status_code, t_submit, t_complete
