"""Provider/model domain types shared by configuration, GUI, and transports."""

from __future__ import annotations

import io
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class InputMode(StrEnum):
    """How audio reaches the transcription service."""

    FILE_UPLOAD = "file_upload"
    LIVE_AUDIO = "live_audio"


@dataclass(frozen=True, order=True)
class ModelRef:
    provider_id: str
    model_id: str

    @property
    def key(self) -> str:
        return f"{self.provider_id}/{self.model_id}"

    @classmethod
    def parse(cls, value: str) -> ModelRef:
        provider_id, separator, model_id = str(value).partition("/")
        if not separator or not provider_id.strip() or not model_id.strip():
            raise ValueError(f"Invalid model reference: {value!r}")
        return cls(provider_id.strip(), model_id.strip())


@dataclass(frozen=True)
class ModeConfig:
    settings: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelConfig:
    id: str
    name: str
    upstream_model: str
    settings: Mapping[str, Any]
    modes: Mapping[InputMode, ModeConfig]
    incremental_output: bool = False
    default_mode: InputMode = InputMode.FILE_UPLOAD

    @property
    def input_modes(self) -> frozenset[InputMode]:
        return frozenset(self.modes)


@dataclass
class ProviderConfig:
    """A provider connection and the models available through it."""

    id: str
    name: str
    type: str
    description: str
    settings: dict[str, Any]
    models: dict[str, ModelConfig]
    enabled: bool = False
    hidden: bool = False
    schema_version: int = 2
    legacy: bool = False


@dataclass(frozen=True)
class ResolvedModel:
    ref: ModelRef
    provider_name: str
    adapter_type: str
    model_name: str
    upstream_model: str
    input_mode: InputMode
    input_modes: frozenset[InputMode]
    incremental_output: bool
    settings: Mapping[str, Any]

    @classmethod
    def create(
        cls,
        provider: ProviderConfig,
        model: ModelConfig,
        input_mode: InputMode,
    ) -> ResolvedModel:
        if input_mode not in model.modes:
            raise ValueError(
                f"Model {provider.id}/{model.id} does not support {input_mode.value}"
            )
        settings = dict(provider.settings)
        settings.update(model.settings)
        settings.update(model.modes[input_mode].settings)
        settings["model"] = settings.get("model") or model.upstream_model
        return cls(
            ref=ModelRef(provider.id, model.id),
            provider_name=provider.name,
            adapter_type=provider.type,
            model_name=model.name,
            upstream_model=str(settings["model"]),
            input_mode=input_mode,
            input_modes=model.input_modes,
            incremental_output=model.incremental_output,
            settings=MappingProxyType(settings),
        )


@dataclass(frozen=True)
class TranscriptionRequest:
    payload_buf: io.BytesIO
    payload_mime: str
    task_id: str
    time_start: float
    record_stop: float
    max_retries: int
    base_delay: float
    model: ResolvedModel
    request_context: Any = None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    status_code: int
    time_submit: float
    time_complete: float
    transport: Mapping[str, Any] = field(default_factory=dict)

    def with_model_metadata(
        self, model: ResolvedModel, *, fell_back: bool = False
    ) -> TranscriptionResult:
        metadata = dict(self.transport)
        metadata.update(
            {
                "provider_id": model.ref.provider_id,
                "model_id": model.ref.model_id,
                "upstream_model": model.upstream_model,
                "input_mode": model.input_mode.value,
                "fell_back": fell_back,
            }
        )
        return TranscriptionResult(
            self.text,
            self.status_code,
            self.time_submit,
            self.time_complete,
            MappingProxyType(metadata),
        )
