"""Qwen Audio Legacy official SDK transport."""
import io
import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Tuple

from src.transcribe.qwen_audio_legacy.response_parser import (
    extract_transcript,
    sdk_response_preview,
)
from src.transcribe.qwen_audio_legacy.settings import (
    build_asr_options,
    build_messages,
    ext_for_mime,
    get_api_key,
    get_model,
    get_timeout_seconds,
)


DEBUG_BUILD = "qwen-audio-legacy-sdk-status-normalize-v2"


def _read_audio_bytes(payload_buf: io.BytesIO) -> bytes:
    try:
        payload_buf.seek(0)
    except Exception:
        pass
    return payload_buf.read()


async def send_with_sdk(
    payload_buf: io.BytesIO,
    payload_mime: str,
) -> Tuple[str, int, float, Dict[str, Any], str | None]:
    """Use the official SDK for Qwen Audio Legacy transcription."""
    import asyncio

    t_complete = time.time()
    meta: Dict[str, Any] = {
        "via": "qwen-audio-legacy-sdk",
        "debug_build": DEBUG_BUILD,
    }
    try:
        import dashscope
    except Exception as exc:
        err = f"{exc.__class__.__name__}: {exc}"
        meta["qwen_audio_legacy_sdk_import_error"] = err
        return "", 503, t_complete, meta, err

    audio_bytes = _read_audio_bytes(payload_buf)
    ext = ext_for_mime(payload_mime)

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f".{ext}") as f:
            f.write(audio_bytes)
            tmp_path = f.name

        audio_uri = str(Path(tmp_path).resolve())
        messages = build_messages(audio_uri)
        model = get_model()
        asr_options = build_asr_options()

        def call_sdk() -> Any:
            return dashscope.MultiModalConversation.call(
                api_key=get_api_key(),
                model=model,
                messages=messages,
                result_format="message",
                asr_options=asr_options,
                request_timeout=get_timeout_seconds(),
            )

        response = await asyncio.wait_for(
            asyncio.to_thread(call_sdk),
            timeout=get_timeout_seconds(),
        )
        t_complete = time.time()

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

        transcript, extra = extract_transcript(payload_data)
        meta.update(extra)
        meta["qwen_audio_legacy_sdk"] = True
        meta["audio_bytes"] = len(audio_bytes)
        meta["audio_uri"] = audio_uri

        status = 200
        response_status = getattr(response, "status_code", None)
        meta["raw_status"] = response_status
        if response_status is not None:
            try:
                candidate_status = int(response_status)
                if 100 <= candidate_status <= 599:
                    status = candidate_status
            except Exception:
                pass
        if isinstance(payload_data, dict):
            try:
                candidate_status = int(payload_data.get("status_code") or status)
                if 100 <= candidate_status <= 599:
                    status = candidate_status
            except Exception:
                pass
        if not transcript:
            preview = sdk_response_preview(response, payload_data)
            if preview:
                meta["qwen_audio_legacy_payload_preview"] = preview
        return transcript, status, t_complete, meta, None if transcript else None
    except Exception as exc:
        t_complete = time.time()
        err = f"{exc.__class__.__name__}: {exc}"
        meta["qwen_audio_legacy_sdk_exception"] = err
        return "", 400, t_complete, meta, err
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
