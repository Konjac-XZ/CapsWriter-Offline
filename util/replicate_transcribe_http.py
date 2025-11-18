import io
import os
import time
import random
from typing import Tuple

from util.client_cosmic import console
from util.openai_transcribe_http import emit_partial_update
from util.provider_settings import (
    get_bool as ps_get_bool,
    get_str as ps_get_str,
)


def _get_model() -> str:
    """Replicate model identifier sourced from provider settings."""
    model = ps_get_str("model", default="gpt-4o-mini-transcribe") or "gpt-4o-mini-transcribe"
    return "openai/" + model.strip()


def _get_input_key() -> str:
    """The input field name for audio on Replicate; default to 'audio_file' for wide compatibility."""
    key = ps_get_str("input_key", default="audio_file")
    return key if key else "audio_file"


def _ensure_token():
    token = ps_get_str("api_token", default=None)
    if not token:
        raise RuntimeError("Replicate API token must be set in config/providers/replicate.yaml")
    # Replicate SDK expects REPLICATE_API_TOKEN in the process environment.
    os.environ["REPLICATE_API_TOKEN"] = token


def _replicate_debug() -> bool:
    return ps_get_bool("debug", default=True)


def _log_stage_elapsed(stage: str, elapsed: float, attempt: int | None = None) -> None:
    """Emit a timing log for a given stage when debug logging is enabled."""
    if not _replicate_debug():
        return
    prefix = "[Replicate Debug]"
    if attempt is not None:
        prefix += f" [attempt {attempt}]"
    console.print(f"{prefix} {stage} took {elapsed:.3f}s", style="dim")


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


def _payload_bytes(payload_buf: io.BytesIO) -> bytes:
    """Extract raw bytes from the provided buffer, rewinding if possible."""
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
    return data


def _make_audio_input(payload_bytes: bytes, payload_mime: str) -> io.BytesIO:
    """Create a BytesIO file-like object suited for Replicate uploads."""
    suffix = _mime_to_suffix(payload_mime)
    bio = io.BytesIO(payload_bytes)
    bio.name = f"audio_file{suffix}"
    return bio


async def _streaming_run(
    audio_input,
    language: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    prompt: str | None = None,
    temperature: float | None = None,
    attempt: int | None = None,
) -> Tuple[str, int, float, float]:
    import replicate

    model = _get_model()
    input_key = _get_input_key()
    try:
        audio_input.seek(0)
    except Exception:
        pass
    _log_send_info(model, input_key, audio_input, None)
    inputs = {"language": language, input_key: audio_input}
    if prompt:
        inputs["prompt"] = prompt
    if temperature is not None:
        inputs["temperature"] = float(temperature)

    t_submit = time.time()
    current = ""
    last_emit = 0.0
    first_event_time = None
    try:
        for event in replicate.stream(model, input=inputs):
            # Each event renders to the next chunk of text
            chunk = str(event)
            if not chunk:
                continue
            if first_event_time is None:
                first_event_time = time.time()
                _log_stage_elapsed("upload stage (stream)", first_event_time - t_submit, attempt)
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
        if first_event_time is None:
            first_event_time = t_complete
            _log_stage_elapsed("upload stage (stream)", first_event_time - t_submit, attempt)
        _log_stage_elapsed("recognition stage (stream)", t_complete - first_event_time, attempt)
        return current, 503, t_submit, t_complete

    t_complete = time.time()
    if first_event_time is None:
        first_event_time = t_complete
        _log_stage_elapsed("upload stage (stream)", first_event_time - t_submit, attempt)
    _log_stage_elapsed("recognition stage (stream)", t_complete - first_event_time, attempt)
    return current, 200, t_submit, t_complete


async def _nonstream_run(
    audio_input,
    language: str,
    prompt: str | None = None,
    temperature: float | None = None,
    attempt: int | None = None,
) -> Tuple[str, int, float, float]:
    import replicate

    model = _get_model()
    input_key = _get_input_key()
    try:
        audio_input.seek(0)
    except Exception:
        pass
    _log_send_info(model, input_key, audio_input, None)
    inputs = {"language": language, input_key: audio_input}
    if prompt:
        inputs["prompt"] = prompt
    if temperature is not None:
        inputs["temperature"] = float(temperature)

    t_submit = time.time()
    current_chunks: list[str] = []
    first_event_time = None
    try:
        for event in replicate.stream(model, input=inputs):
            chunk = str(event)
            if not chunk:
                continue
            if first_event_time is None:
                first_event_time = time.time()
                _log_stage_elapsed("upload stage (non-stream)", first_event_time - t_submit, attempt)
            current_chunks.append(chunk)
        t_complete = time.time()
        if first_event_time is None:
            first_event_time = t_complete
            _log_stage_elapsed("upload stage (non-stream)", first_event_time - t_submit, attempt)
        _log_stage_elapsed("recognition stage (non-stream)", t_complete - first_event_time, attempt)
        text = "".join(current_chunks)
        return text, 200, t_submit, t_complete
    except Exception as e:
        t_complete = time.time()
        if first_event_time is None:
            first_event_time = t_complete
            _log_stage_elapsed("upload stage (non-stream)", first_event_time - t_submit, attempt)
        _log_stage_elapsed("recognition stage (non-stream)", t_complete - first_event_time, attempt)
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
    payload_bytes = _payload_bytes(payload_buf)

    text_result = ""
    status_code = 0
    t_submit = time.time()
    t_complete = t_submit

    # Send audio directly to Replicate. Streaming is attempted once; fall back to sync with retries.
    for attempt in range(max_retries):
        try:
            if enable_stream_pref and attempt == 0:
                stream_input = _make_audio_input(payload_bytes, payload_mime)
                text_result, status_code, t_submit, t_complete = await _streaming_run(
                    stream_input,
                    language,
                    task_id,
                    time_start,
                    record_stop,
                    prompt,
                    temperature,
                    attempt + 1,
                )
                if status_code == 200:
                    break
            # Non-streaming path or retry after streaming failure
            run_input = _make_audio_input(payload_bytes, payload_mime)
            text_result, status_code, t_submit, t_complete = await _nonstream_run(
                run_input, language, prompt, temperature, attempt + 1
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
