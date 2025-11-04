from __future__ import annotations

import io
from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple

from util.provider_settings import (
    get_str as ps_get_str,
    get_bool as ps_get_bool,
)


class TranscriptionProvider(ABC):
    """Abstract interface for a transcription provider."""

    @abstractmethod
    def name(self) -> str:
        """Human-friendly provider name."""

    @abstractmethod
    async def transcribe(
        self,
        payload_buf: io.BytesIO,
        payload_mime: str,
        task_id: str,
        time_start: float,
        record_stop: float,
        max_retries: int,
        base_delay: float,
    ) -> Tuple[str, int, float, float, Dict[str, Any]]:
        """Return (text, status_code, t_submit, t_complete, transport_meta)."""


class OpenAIProvider(TranscriptionProvider):
    def name(self) -> str:
        return "openai"

    async def transcribe(
        self,
        payload_buf: io.BytesIO,
        payload_mime: str,
        task_id: str,
        time_start: float,
        record_stop: float,
        max_retries: int,
        base_delay: float,
    ) -> Tuple[str, int, float, float, Dict[str, Any]]:
        from util.openai_transcribe_http import (
            get_api_base,
            get_model as _get_model,
            get_prompt as _get_prompt,
            get_language as _get_language,
            get_temperature as _get_temperature,
            is_streaming_enabled as _get_stream_flag,
            transcribe_with_retries as _openai_transcribe_with_retries,
        )

        api_base = get_api_base()
        url = f"{api_base}/v1/audio/transcriptions"
        # Build base form fields; allow providers to omit response_format if upstream adds it automatically
        data_form_base = {
            "model": _get_model(),
            "prompt": _get_prompt(),
            "language": _get_language(),
        }
        # Only include response_format if not explicitly omitted by provider configuration
        if not ps_get_bool("openai_omit_response_format", default=False):
            data_form_base["response_format"] = (
                ps_get_str("response_format", env="OPENAI_TRANSCRIBE_FORMAT", default="text") or "text"
            )
        _temp = _get_temperature()
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


class ReplicateProvider(TranscriptionProvider):
    def name(self) -> str:
        return "replicate"

    async def transcribe(
        self,
        payload_buf: io.BytesIO,
        payload_mime: str,
        task_id: str,
        time_start: float,
        record_stop: float,
        max_retries: int,
        base_delay: float,
    ) -> Tuple[str, int, float, float, Dict[str, Any]]:
        from util.replicate_transcribe_http import transcribe_with_retries as rep_transcribe
        from util.openai_transcribe_http import is_streaming_enabled as _get_stream_flag
        import os

        enable_stream_pref = _get_stream_flag()
        language = ps_get_str("language", env="OPENAI_TRANSCRIBE_LANGUAGE", default="zh") or "zh"
        prompt = ps_get_str("prompt", env="TRANSCRIBE_PROMPT", default="") or ""
        # Parse numeric temperature
        temp = ps_get_str("temperature", env="TRANSCRIBE_TEMPERATURE", default=None)
        try:
            temperature = float(temp) if temp is not None and str(temp).strip() != "" else None
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


class ElevenLabsProvider(TranscriptionProvider):
    def name(self) -> str:
        return "elevenlabs"

    async def transcribe(
        self,
        payload_buf: io.BytesIO,
        payload_mime: str,
        task_id: str,
        time_start: float,
        record_stop: float,
        max_retries: int,
        base_delay: float,
    ) -> Tuple[str, int, float, float, Dict[str, Any]]:
        from util.elevenlabs_transcribe_http import (
            transcribe_with_retries as elevenlabs_transcribe,
        )

        text_result, status_code, t_submit, t_complete, http2_flag = await elevenlabs_transcribe(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )
        return text_result, status_code, t_submit, t_complete, {"http2": http2_flag}


class DashScopeProvider(TranscriptionProvider):
    def name(self) -> str:
        return "dashscope"

    async def transcribe(
        self,
        payload_buf: io.BytesIO,
        payload_mime: str,
        task_id: str,
        time_start: float,
        record_stop: float,
        max_retries: int,
        base_delay: float,
    ) -> Tuple[str, int, float, float, Dict[str, Any]]:
        from util.dashscope_transcribe_http import (
            transcribe_with_retries as dashscope_transcribe,
        )

        text_result, status_code, t_submit, t_complete, transport_meta = await dashscope_transcribe(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )
        return text_result, status_code, t_submit, t_complete, transport_meta


class SonioxProvider(TranscriptionProvider):
    def name(self) -> str:
        return "soniox"

    async def transcribe(
        self,
        payload_buf: io.BytesIO,
        payload_mime: str,
        task_id: str,
        time_start: float,
        record_stop: float,
        max_retries: int,
        base_delay: float,
    ) -> Tuple[str, int, float, float, Dict[str, Any]]:
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


def make_provider(kind: str) -> TranscriptionProvider:
    kind = (kind or "").strip().lower()
    if kind in ("openai", "oai"):
        return OpenAIProvider()
    if kind == "replicate":
        return ReplicateProvider()
    if kind in ("elevenlabs", "eleven-labs", "xi"):
        return ElevenLabsProvider()
    if kind in ("dashscope", "alibabacloud"):
        return DashScopeProvider()
    if kind in ("soniox", "soniox-rest", "soniox_http"):
        return SonioxProvider()
    # Default
    return OpenAIProvider()
