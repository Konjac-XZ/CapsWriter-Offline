import os
import io
from typing import Tuple, Dict, Any

from util.transcribe.openai.openai_transcribe_http import (
    is_streaming_enabled as _get_stream_flag,
)
from util.providers import make_provider

# Import provider manager for dynamic configuration
try:
    from util.provider_config import provider_manager
    PROVIDER_MANAGER_AVAILABLE = True
except ImportError:
    PROVIDER_MANAGER_AVAILABLE = False


def get_stream_flag() -> bool:
    """Provider-agnostic accessor for the streaming flag (keeps current env compatibility)."""
    return _get_stream_flag()


def initialize_providers() -> None:
    """Initialize provider configurations on startup."""
    if PROVIDER_MANAGER_AVAILABLE:
        # Load providers and set active one in environment
        provider_manager.load_providers()
        active_provider = provider_manager.get_active_provider()
        if active_provider:
            # Set environment variables based on active provider
            provider_id = None
            for pid, provider in provider_manager.providers.items():
                if provider.enabled:
                    provider_id = pid
                    break
            
            if provider_id:
                provider_manager.set_active_provider(provider_id)


async def _run_openai(*args, **kwargs):
    provider = make_provider("openai")
    return await provider.transcribe(*args)


async def _run_replicate(*args, **kwargs):
    provider = make_provider("replicate")
    return await provider.transcribe(*args)


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
    # Normalize payload buffer: accept bytes/bytearray/memoryview or file-like with read/seek
    if isinstance(payload_buf, (bytes, bytearray, memoryview)):
        payload_buf = io.BytesIO(bytes(payload_buf))

    provider = None
    if PROVIDER_MANAGER_AVAILABLE:
        try:
            ptype = provider_manager.get_active_provider_type()
            provider = (ptype or "").strip().lower() or None
        except Exception:
            provider = None
    if not provider:
        # Back-compat fallback
        provider = os.getenv("TRANSCRIBE_PROVIDER", "openai").strip().lower()
    if provider in (
        "openai",
        "replicate",
        "elevenlabs",
        "dashscope",
        "alibabacloud",
        "soniox",
        "soniox-rest",
        "soniox_http",
        "gemini",
        "google-gemini",
        "google",
    ):
        prov = make_provider(provider)
        return await prov.transcribe(
            payload_buf, payload_mime, task_id, time_start, record_stop, max_retries, base_delay
        )
    # Fallback to openai for unknown values
    prov = make_provider("openai")
    return await prov.transcribe(
        payload_buf, payload_mime, task_id, time_start, record_stop, max_retries, base_delay
    )
