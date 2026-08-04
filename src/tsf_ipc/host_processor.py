from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from .protocol import CompositionStyle


@dataclass(frozen=True, slots=True)
class HostApplication:
    process_id: int = 0
    process_name: str | None = None


class RevisionDecision(Enum):
    APPLY = "apply"
    DEFER_FINAL = "defer_final"


class HostProcessor(Protocol):
    name: str
    trust_external_termination_rollback: bool

    def matches(self, host: HostApplication) -> bool: ...

    def process_revision(
        self,
        text: str,
        style: CompositionStyle,
    ) -> RevisionDecision: ...


class DefaultHostProcessor:
    name = "default"
    trust_external_termination_rollback = True

    def matches(self, host: HostApplication) -> bool:
        return True

    def process_revision(
        self,
        text: str,
        style: CompositionStyle,
    ) -> RevisionDecision:
        return RevisionDecision.APPLY


class ChatGptHostProcessor:
    name = "chatgpt_desktop"
    trust_external_termination_rollback = False

    def matches(self, host: HostApplication) -> bool:
        return (host.process_name or "").casefold() == "chatgpt.exe"

    def process_revision(
        self,
        text: str,
        style: CompositionStyle,
    ) -> RevisionDecision:
        if "\n" in text or "\r" in text:
            return RevisionDecision.DEFER_FINAL
        return RevisionDecision.APPLY


class HostProcessorRegistry:
    def __init__(
        self,
        processors: tuple[HostProcessor, ...] | None = None,
        default: HostProcessor | None = None,
    ) -> None:
        self._processors = (
            processors if processors is not None else (ChatGptHostProcessor(),)
        )
        self._default = default or DefaultHostProcessor()

    def resolve(self, host: HostApplication) -> HostProcessor:
        for processor in self._processors:
            if processor.matches(host):
                return processor
        return self._default


DEFAULT_HOST_PROCESSOR_REGISTRY = HostProcessorRegistry()
