from __future__ import annotations

import io
from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple

from src.provider.provider_settings import (
    get_str as ps_get_str,
    get_bool as ps_get_bool,
)
from src.transcribe.streaming import StreamingTranscriptionSession


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

    def supports_streaming_input(self) -> bool:
        return False

    def create_streaming_session(
        self,
        task_id: str,
        time_start: float,
    ) -> StreamingTranscriptionSession:
        raise NotImplementedError(f"{self.name()} does not support streaming input")


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
        from src.transcribe.openai.openai_transcribe_http import (
            get_api_base,
            get_model as _get_model,
            get_prompt as _get_prompt,
            get_language as _get_language,
            get_temperature as _get_temperature,
            is_incremental_results_enabled as _get_incremental_results_flag,
            transcribe_with_retries as _openai_transcribe_with_retries,
        )

        api_base = get_api_base()
        url = f"{api_base}/v1/audio/transcriptions"
        # Build base form fields; allow providers to omit response_format if upstream adds it automatically
        data_form_base: dict[str, Any] = {
            "model": _get_model(),
            "prompt": _get_prompt(),
            "language": _get_language(),
        }
        # Only include response_format if not explicitly omitted by provider configuration
        if not ps_get_bool("openai_omit_response_format", default=False):
            data_form_base["response_format"] = (
                ps_get_str(
                    "response_format", env="OPENAI_TRANSCRIBE_FORMAT", default="text"
                )
                or "text"
            )
        _temp = _get_temperature()
        if _temp is not None:
            data_form_base["temperature"] = _temp
        enable_incremental_results = _get_incremental_results_flag()

        (
            text_result,
            status_code,
            t_submit,
            t_complete,
            http2_flag,
        ) = await _openai_transcribe_with_retries(
            payload_buf,
            payload_mime,
            data_form_base,
            url,
            enable_incremental_results,
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
        from src.transcribe.replicate.replicate_transcribe_http import (
            transcribe_with_retries as rep_transcribe,
        )
        from src.transcribe.openai.openai_transcribe_http import (
            is_incremental_results_enabled as _get_incremental_results_flag,
        )

        enable_incremental_results = _get_incremental_results_flag()
        language = (
            ps_get_str("language", env="OPENAI_TRANSCRIBE_LANGUAGE", default="zh")
            or "zh"
        )
        prompt = ps_get_str("prompt", env="TRANSCRIBE_PROMPT", default="") or ""
        # Parse numeric temperature
        temp = ps_get_str("temperature", env="TRANSCRIBE_TEMPERATURE", default=None)
        try:
            temperature = (
                float(temp) if temp is not None and str(temp).strip() != "" else None
            )
        except Exception:
            temperature = None

        text_result, status_code, t_submit, t_complete = await rep_transcribe(
            payload_buf,
            payload_mime,
            language,
            enable_incremental_results,
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
        from src.transcribe.elevenlabs.elevenlabs_transcribe_http import (
            transcribe_with_retries as elevenlabs_transcribe,
        )

        (
            text_result,
            status_code,
            t_submit,
            t_complete,
            http2_flag,
        ) = await elevenlabs_transcribe(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )
        return text_result, status_code, t_submit, t_complete, {"http2": http2_flag}


class QwenAudioLegacyProvider(TranscriptionProvider):
    def name(self) -> str:
        return "qwen-audio-legacy"

    def supports_streaming_input(self) -> bool:
        from src.transcribe.qwen_audio_legacy.settings import should_use_realtime

        return should_use_realtime()

    def create_streaming_session(
        self,
        task_id: str,
        time_start: float,
    ) -> StreamingTranscriptionSession:
        from src.transcribe.qwen_audio_legacy.qwen_audio_legacy_transcribe_sdk import (
            QwenAudioLegacyRealtimeSession,
        )

        return QwenAudioLegacyRealtimeSession(task_id, time_start)

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
        from src.transcribe.qwen_audio_legacy.qwen_audio_legacy_transcribe_http import (
            transcribe_with_retries as qwen_audio_legacy_transcribe,
        )

        (
            text_result,
            status_code,
            t_submit,
            t_complete,
            transport_meta,
        ) = await qwen_audio_legacy_transcribe(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )
        return text_result, status_code, t_submit, t_complete, transport_meta


class QwenAudioProvider(TranscriptionProvider):
    """Qwen Audio 3.0 streaming SDK provider with HTTP fallback."""

    def name(self) -> str:
        return "qwen-audio"

    def supports_streaming_input(self) -> bool:
        from src.transcribe.qwen_audio.qwen_audio_transcribe_sdk import (
            should_use_realtime,
        )

        return should_use_realtime()

    def create_streaming_session(
        self,
        task_id: str,
        time_start: float,
    ) -> StreamingTranscriptionSession:
        from src.transcribe.qwen_audio.qwen_audio_transcribe_sdk import (
            QwenAudioStreamingSession,
        )

        return QwenAudioStreamingSession(task_id, time_start)

    async def transcribe(
        self,
        payload_buf: io.BytesIO,
        payload_mime: str,
        task_id: str,
        time_start: float,
        record_stop: float,
        max_retries: int,
        base_delay: float,
        request_context: Any = None,
    ) -> Tuple[str, int, float, float, Dict[str, Any]]:
        from src.transcribe.qwen_audio.qwen_audio_transcribe_http import (
            transcribe_with_retries,
        )

        return await transcribe_with_retries(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
            request_context,
        )


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
        from src.transcribe.soniox.soniox_transcribe_http import (
            transcribe_with_retries as soniox_transcribe,
        )

        (
            text_result,
            status_code,
            t_submit,
            t_complete,
            http2_flag,
        ) = await soniox_transcribe(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )
        return text_result, status_code, t_submit, t_complete, {"http2": http2_flag}


class GeminiProvider(TranscriptionProvider):
    def name(self) -> str:
        return "gemini"

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
        from src.transcribe.gemini.gemini_transcribe_http import (
            transcribe_with_retries as gemini_transcribe,
        )

        (
            text_result,
            status_code,
            t_submit,
            t_complete,
            http2_flag,
        ) = await gemini_transcribe(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )
        return text_result, status_code, t_submit, t_complete, {"http2": http2_flag}


class OpenRouterProvider(TranscriptionProvider):
    def name(self) -> str:
        return "openrouter"

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
        from src.transcribe.openrouter.openrouter_transcribe_http import (
            transcribe_with_retries as openrouter_transcribe,
        )

        (
            text_result,
            status_code,
            t_submit,
            t_complete,
            http2_flag,
        ) = await openrouter_transcribe(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )
        return text_result, status_code, t_submit, t_complete, {"http2": http2_flag}


class XiaomiProvider(TranscriptionProvider):
    def name(self) -> str:
        return "xiaomi"

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
        from src.transcribe.xiaomi.xiaomi_transcribe_http import (
            transcribe_with_retries as xiaomi_transcribe,
        )

        (
            text_result,
            status_code,
            t_submit,
            t_complete,
            http2_flag,
        ) = await xiaomi_transcribe(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )
        return text_result, status_code, t_submit, t_complete, {"http2": http2_flag}


class ByteDanceProvider(TranscriptionProvider):
    def name(self) -> str:
        return "bytedance"

    def supports_streaming_input(self) -> bool:
        from src.transcribe.bytedance.bytedance_transcribe_ws import (
            should_use_realtime,
        )

        return should_use_realtime()

    def create_streaming_session(
        self,
        task_id: str,
        time_start: float,
    ) -> StreamingTranscriptionSession:
        from src.transcribe.bytedance.bytedance_transcribe_ws import (
            ByteDanceStreamingSession,
        )

        return ByteDanceStreamingSession(task_id, time_start)

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
        from src.transcribe.bytedance.bytedance_transcribe_http import (
            transcribe_with_retries,
        )

        return await transcribe_with_retries(
            payload_buf,
            payload_mime,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )


def make_provider(kind: str) -> TranscriptionProvider:
    kind = (kind or "").strip().lower()
    if kind in ("openai", "oai"):
        return OpenAIProvider()
    if kind == "replicate":
        return ReplicateProvider()
    if kind in ("elevenlabs", "eleven-labs", "xi"):
        return ElevenLabsProvider()
    if kind in ("qwen-audio-legacy", "dashscope", "alibabacloud"):
        return QwenAudioLegacyProvider()
    if kind in (
        "qwen-audio",
        "qwen_audio_3",
        "qwen-audio-3",
        "qwen_audio",
        "alibaba_qwen_audio_3",
    ):
        return QwenAudioProvider()
    if kind in ("soniox", "soniox-rest", "soniox_http"):
        return SonioxProvider()
    if kind in ("gemini", "google-gemini", "google"):
        return GeminiProvider()
    if kind in ("openrouter", "open-router"):
        return OpenRouterProvider()
    if kind in ("xiaomi", "mimo", "xiaomi-mimo"):
        return XiaomiProvider()
    if kind in ("bytedance", "doubao", "volcengine", "volc"):
        return ByteDanceProvider()
    # Default
    return OpenAIProvider()
