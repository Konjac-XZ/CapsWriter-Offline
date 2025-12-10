import io
import json
import os
import random
import time
from typing import Tuple

from util.client_cosmic import console
from util.openai_transcribe_http import emit_partial_update
from util.provider_config import provider_manager


def _get_ws_url() -> str:
    return os.getenv("SONIOX_WS_URL", "wss://stt-rt.soniox.com/transcribe-websocket").strip()


def _get_api_key() -> str:
    key = os.getenv("SONIOX_API_KEY") or os.getenv("SONIOX_TEMP_API_KEY")
    if not key:
        raise RuntimeError("SONIOX_API_KEY (or SONIOX_TEMP_API_KEY) is required for provider=soniox")
    return key


def _get_model() -> str:
    # Prefer explicit SONIOX_MODEL, else fallback to TRANSCRIBE_MODEL if plausible, else default
    m = os.getenv("SONIOX_MODEL")
    if m and m.strip():
        return m.strip()
    tm = os.getenv("TRANSCRIBE_MODEL", "").strip()
    if tm:
        return tm
    return "stt-rt-preview"


def _get_language_hints() -> list[str] | None:
    # Comma-separated languages or reuse OPENAI_TRANSCRIBE_LANGUAGE
    raw = os.getenv("SONIOX_LANGUAGE_HINTS")
    if raw and raw.strip():
        parts = [p.strip() for p in raw.split(",")]
        return [p for p in parts if p]
    one = os.getenv("OPENAI_TRANSCRIBE_LANGUAGE")
    if one and one.strip():
        return [one.strip()]
    return None


def _get_context() -> str | None:
    # Reuse provider-agnostic prompt as context to bias recognition
    ctx = provider_manager.get_provider_prompt()
    if ctx and ctx.strip():
        return ctx
    return None


def _get_enable_non_final_tokens(enable_stream_pref: bool) -> bool:
    # Expose override but default to following the app streaming preference
    raw = os.getenv("SONIOX_ENABLE_NON_FINAL_TOKENS")
    if raw is None:
        return bool(enable_stream_pref)
    return raw.strip() not in ("0", "false", "False")


def _get_enable_endpoint_detection() -> bool:
    return os.getenv("SONIOX_ENABLE_ENDPOINT_DETECTION", "1").strip() not in ("0", "false", "False")


def _get_enable_diarization() -> bool:
    return os.getenv("SONIOX_ENABLE_DIARIZATION", "0").strip() not in ("0", "false", "False")


async def _ws_transcribe(
    payload_buf: io.BytesIO,
    enable_stream_pref: bool,
    task_id: str,
    time_start: float,
    record_stop: float,
) -> Tuple[str, int, float, float]:
    import websockets

    url = _get_ws_url()
    api_key = _get_api_key()
    model = _get_model()
    lang_hints = _get_language_hints()
    context = _get_context()
    non_final = _get_enable_non_final_tokens(enable_stream_pref)
    endpoint_det = _get_enable_endpoint_detection()
    diar = _get_enable_diarization()

    # Build config message per Soniox docs
    cfg: dict = {
        "api_key": api_key,
        "model": model,
        "audio_format": "auto",
        "enable_non_final_tokens": bool(non_final),
        "enable_endpoint_detection": bool(endpoint_det),
        "enable_speaker_diarization": bool(diar),
        "client_reference_id": task_id,
    }
    if lang_hints:
        cfg["language_hints"] = lang_hints
    if context:
        cfg["context"] = context

    # Prepare audio bytes
    try:
        payload_buf.seek(0)
    except Exception:
        pass
    try:
        audio_bytes = payload_buf.getvalue()
    except Exception:
        audio_bytes = payload_buf.read()

    if not audio_bytes:
        return "", 400, time.time(), time.time()

    t_submit = time.time()
    current_text = ""
    last_emit = 0.0
    status_code = 200

    try:
        async with websockets.connect(url, max_size=None, ping_interval=20) as ws:
            await ws.send(json.dumps(cfg))

            # Send the full audio buffer as a binary frame
            await ws.send(audio_bytes)

            # End the stream: empty frame. Some servers accept empty binary or text.
            try:
                await ws.send(b"")
            except Exception:
                try:
                    await ws.send("")
                except Exception:
                    pass

            # Receive responses until finished or close
            while True:
                try:
                    msg = await ws.recv()
                except websockets.exceptions.ConnectionClosedOK:
                    break
                except websockets.exceptions.ConnectionClosedError:
                    # Treat as transient failure
                    status_code = 503
                    break

                if isinstance(msg, (bytes, bytearray)):
                    # Unexpected binary response; ignore
                    continue

                s = msg.strip()
                if not s:
                    continue
                try:
                    obj = json.loads(s)
                except Exception:
                    # Best effort: append raw
                    current_text += s
                    continue

                if isinstance(obj, dict):
                    if obj.get("error_code"):
                        try:
                            status_code = int(obj.get("error_code", 500))
                        except Exception:
                            status_code = 500
                        # Optionally log error message
                        err = obj.get("error_message") or ""
                        if err:
                            try:
                                console.print(f"Soniox 错误：{status_code} {err}", style="bright_red")
                            except Exception:
                                pass
                        break

                    tokens = obj.get("tokens")
                    if isinstance(tokens, list):
                        # Rebuild current text from tokens each message to avoid duplication
                        try:
                            current_text = "".join(
                                t.get("text", "") for t in tokens if isinstance(t, dict)
                            )
                        except Exception:
                            # Fallback: stringify
                            parts = []
                            for t in tokens:
                                if isinstance(t, dict) and isinstance(t.get("text"), str):
                                    parts.append(t["text"])
                            current_text = "".join(parts)

                        if enable_stream_pref and current_text:
                            now = time.time()
                            if now - last_emit >= 0.05:
                                last_emit = now
                                await emit_partial_update(task_id, current_text, time_start, record_stop, t_submit)

                    if obj.get("finished") is True:
                        break

    except Exception as e:
        try:
            console.print(f"Soniox WebSocket 异常：{e}", style="bright_yellow")
        except Exception:
            pass
        if status_code == 200:
            status_code = 503

    t_complete = time.time()
    return current_text, status_code, t_submit, t_complete


async def transcribe_with_retries(
    payload_buf: io.BytesIO,
    payload_mime: str,
    enable_stream_pref: bool,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
) -> Tuple[str, int, float, float]:
    """Retry wrapper for Soniox WebSocket streaming transcription.

    Returns (text_result, status_code, t_submit, t_complete).
    """
    text_result = ""
    status_code = 0
    t_submit = time.time()
    t_complete = t_submit

    # Import exceptions locally to avoid hard dependency if provider isn't used
    try:
        import websockets
        from websockets.exceptions import ConnectionClosedError, ConnectionClosedOK
    except Exception:
        websockets = None  # type: ignore
        ConnectionClosedError = Exception  # type: ignore
        ConnectionClosedOK = Exception  # type: ignore

    for attempt in range(max_retries):
        # Reset buffer position each attempt
        try:
            payload_buf.seek(0)
        except Exception:
            pass

        try:
            t_submit = time.time()
            text_result, status_code, _, t_complete = await _ws_transcribe(
                payload_buf, enable_stream_pref, task_id, time_start, record_stop
            )
            # If success or client error, stop retrying
            if status_code < 500 and status_code not in (408, 429):
                break
        except (OSError, Exception) as e:
            t_complete = time.time()
            try:
                console.print(
                    f"Soniox 重试（第 {attempt + 1}/{max_retries} 次）异常：{e}", style="bright_yellow"
                )
            except Exception:
                pass

        if attempt + 1 >= max_retries:
            break
        delay = base_delay * (2 ** attempt) + random.uniform(0.0, 0.1)
        import asyncio

        await asyncio.sleep(delay)

    return text_result, status_code, t_submit, t_complete
