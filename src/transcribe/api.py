import io
from typing import Any, Dict, Tuple

from src.provider.domain import ResolvedModel, TranscriptionRequest
from src.transcribe.openai.openai_transcribe_http import (
    is_incremental_results_enabled as _get_incremental_results_flag,
)
from src.transcribe.providers import make_provider

# Import provider manager for dynamic configuration
try:
    from src.provider.provider_config import provider_manager

    PROVIDER_MANAGER_AVAILABLE = True
except ImportError:
    provider_manager: Any | None = None
    PROVIDER_MANAGER_AVAILABLE = False


def get_incremental_results_flag() -> bool:
    """Whether providers should request incremental transcript responses."""
    return _get_incremental_results_flag()


def get_stream_flag() -> bool:
    """Backward-compatible alias for incremental transcript responses."""
    return get_incremental_results_flag()


def initialize_providers() -> None:
    """Initialize provider configurations on startup."""
    if provider_manager is not None:
        provider_manager.load_providers()


async def transcribe_audio(
    payload_buf,
    payload_mime: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
    request_context: Any = None,
    resolved_model: ResolvedModel | None = None,
) -> Tuple[str, int, float, float, Dict[str, Any]]:
    """Provider-agnostic transcription entry point.

    Returns (text, status_code, t_submit, t_complete, transport_info_dict)
    """
    # Normalize payload buffer: accept bytes/bytearray/memoryview or file-like with read/seek
    if isinstance(payload_buf, (bytes, bytearray, memoryview)):
        payload_buf = io.BytesIO(bytes(payload_buf))

    if resolved_model is None:
        if provider_manager is None:
            raise RuntimeError("Transcription provider catalog is unavailable")
        resolved_model = provider_manager.resolve_model()
    provider = make_provider(resolved_model.adapter_type)
    request = TranscriptionRequest(
        payload_buf=payload_buf,
        payload_mime=payload_mime,
        task_id=task_id,
        time_start=time_start,
        record_stop=record_stop,
        max_retries=max_retries,
        base_delay=base_delay,
        request_context=request_context,
        model=resolved_model,
    )
    result = await provider.transcribe_request(request)
    return (
        result.text,
        result.status_code,
        result.time_submit,
        result.time_complete,
        dict(result.transport),
    )
