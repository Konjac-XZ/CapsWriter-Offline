import asyncio
import io
from types import MappingProxyType

import pytest

from src.provider.domain import (
    InputMode,
    ModelRef,
    ResolvedModel,
    TranscriptionRequest,
)
from src.provider.provider_settings import get_str
from src.transcribe.providers import TranscriptionProvider, make_provider


def _resolved(mode=InputMode.FILE_UPLOAD) -> ResolvedModel:
    return ResolvedModel(
        ref=ModelRef("alpha", "shared"),
        provider_name="Alpha",
        adapter_type="openai",
        model_name="Shared ASR",
        upstream_model="upstream-model",
        input_mode=mode,
        input_modes=frozenset({InputMode.FILE_UPLOAD, InputMode.LIVE_AUDIO}),
        incremental_output=False,
        settings=MappingProxyType({"model": "upstream-model", "marker": "bound"}),
    )


class _Adapter(TranscriptionProvider):
    def name(self) -> str:
        return "openai"

    async def transcribe(
        self,
        payload_buf,
        payload_mime,
        task_id,
        time_start,
        record_stop,
        max_retries,
        base_delay,
    ):
        assert get_str("marker") == "bound"
        return "text", 200, 1.0, 2.0, {"transport": "fake"}


def test_unified_request_binds_settings_and_normalizes_result_metadata():
    model = _resolved()
    request = TranscriptionRequest(
        payload_buf=io.BytesIO(b"audio"),
        payload_mime="audio/wav",
        task_id="task",
        time_start=0.0,
        record_stop=1.0,
        max_retries=1,
        base_delay=0.0,
        model=model,
    )

    result = asyncio.run(_Adapter().transcribe_request(request))

    assert result.text == "text"
    assert result.transport == {
        "transport": "fake",
        "provider_id": "alpha",
        "model_id": "shared",
        "upstream_model": "upstream-model",
        "input_mode": "file_upload",
        "fell_back": False,
    }


def test_unknown_adapter_is_an_explicit_error():
    with pytest.raises(ValueError, match="Unknown transcription provider adapter"):
        make_provider("not-a-provider")
