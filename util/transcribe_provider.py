import os
from typing import Tuple, Dict, Any

from util.openai_transcribe_http import (
    get_api_base as _openai_get_api_base,
    get_model as _openai_get_model,
    get_prompt as _openai_get_prompt,
    get_temperature as _openai_get_temperature,
    get_language as _openai_get_language,
    is_streaming_enabled as _get_stream_flag,
    transcribe_with_retries as _openai_transcribe_with_retries,
)


def get_stream_flag() -> bool:
    """Provider-agnostic accessor for the streaming flag (keeps current env compatibility)."""
    return _get_stream_flag()


async def _run_openai(
    payload_buf,
    payload_mime: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
) -> Tuple[str, int, float, float, Dict[str, Any]]:
    """Execute transcription via the existing OpenAI-compatible HTTP endpoint."""
    api_base = _openai_get_api_base()
    url = f"{api_base}/v1/audio/transcriptions"
    data_form_base = {
        "model": _openai_get_model(),
        "prompt": _openai_get_prompt(),
        "response_format": os.getenv("OPENAI_TRANSCRIBE_FORMAT", "text"),
        "language": _openai_get_language(),
    }
    # Optional temperature (provider-agnostic env)
    _temp = _openai_get_temperature()
    if _temp is not None:
        data_form_base["temperature"] = _temp
    enable_stream_pref = _get_stream_flag()

    text_result, status_code, t_submit, t_complete, http2_flag = await _openai_transcribe_with_retries(
        payload_buf,
        payload_mime,
        data_form_base,
        url,
        enable_stream_pref,
        task_id,
        time_start,
        record_stop,
        max_retries,
        base_delay,
    )
    return text_result, status_code, t_submit, t_complete, {"http2": http2_flag}


async def _run_replicate(
    payload_buf,
    payload_mime: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
) -> Tuple[str, int, float, float, Dict[str, Any]]:
    """Execute transcription via Replicate provider using their streaming API when enabled."""
    from util.replicate_transcribe_http import transcribe_with_retries as rep_transcribe

    enable_stream_pref = _get_stream_flag()
    language = os.getenv("OPENAI_TRANSCRIBE_LANGUAGE", "zh")
    prompt = _openai_get_prompt()
    # Parse numeric temperature if set
    _temp_env = os.getenv("TRANSCRIBE_TEMPERATURE")
    temperature = None
    if _temp_env is not None and _temp_env.strip() != "":
        try:
            temperature = float(_temp_env)
        except Exception:
            temperature = None

    text_result, status_code, t_submit, t_complete = await rep_transcribe(
        payload_buf,
        payload_mime,
        language,
        enable_stream_pref,
        task_id,
        time_start,
        record_stop,
        max_retries,
        base_delay,
        prompt,
        temperature,
    )
    return text_result, status_code, t_submit, t_complete, {"http2": False}


async def transcribe_audio(
    payload_buf,
    payload_mime: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
) -> Tuple[str, int, float, float, Dict[str, Any]]:
    """Provider-agnostic transcription entry point.

    Returns (text, status_code, t_submit, t_complete, transport_info_dict)
    """
    provider = os.getenv("TRANSCRIBE_PROVIDER", "openai").strip().lower()
    if provider == "openai":
        return await _run_openai(
            payload_buf, payload_mime, task_id, time_start, record_stop, max_retries, base_delay
        )
    if provider == "replicate":
        return await _run_replicate(
            payload_buf, payload_mime, task_id, time_start, record_stop, max_retries, base_delay
        )
    if provider in ("soniox", "soniox-rest", "soniox_http"):
        from util.soniox_transcribe_http import (
            transcribe_with_retries as soniox_transcribe,
        )
        text_result, status_code, t_submit, t_complete, http2_flag = await soniox_transcribe(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )
        return text_result, status_code, t_submit, t_complete, {"http2": http2_flag}
    if provider in ("elevenlabs", "11labs", "11l"):
        from util.elevenlabs_transcribe_http import (
            transcribe_with_retries as el_transcribe,
        )
        text_result, status_code, t_submit, t_complete, http2_flag = await el_transcribe(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )
        return text_result, status_code, t_submit, t_complete, {"http2": http2_flag}
    # Fallback to openai for unknown values
    return await _run_openai(
        payload_buf, payload_mime, task_id, time_start, record_stop, max_retries, base_delay
    )
