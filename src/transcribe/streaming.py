from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Tuple

import numpy as np


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
