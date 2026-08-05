from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple

import numpy as np

from src.provider.domain import ResolvedModel
from src.provider.provider_settings import use_resolved_model


class StreamingTranscriptionSession(ABC):
    """Provider-neutral live audio transcription session."""

    @abstractmethod
    async def start(self) -> None:
        """Open the remote session before audio frames are sent."""

    @abstractmethod
    async def send_audio(self, audio_chunk: np.ndarray) -> None:
        """Send one float32 audio chunk captured at 48 kHz."""

    @abstractmethod
    async def finish(self) -> Tuple[str, int, float, float, Dict[str, Any]]:
        """Finish input and return final transcript tuple."""

    @abstractmethod
    async def cancel(self) -> None:
        """Best-effort cancellation/cleanup."""


class BoundStreamingTranscriptionSession(StreamingTranscriptionSession):
    """Bind immutable model settings for every operation on a legacy session."""

    def __init__(
        self, delegate: StreamingTranscriptionSession, model: ResolvedModel
    ) -> None:
        self._delegate = delegate
        self._model = model

    async def start(self) -> None:
        with use_resolved_model(self._model):
            await self._delegate.start()

    async def send_audio(self, audio_chunk: np.ndarray) -> None:
        with use_resolved_model(self._model):
            await self._delegate.send_audio(audio_chunk)

    async def finish(self) -> Tuple[str, int, float, float, Dict[str, Any]]:
        with use_resolved_model(self._model):
            return await self._delegate.finish()

    async def cancel(self) -> None:
        with use_resolved_model(self._model):
            await self._delegate.cancel()
