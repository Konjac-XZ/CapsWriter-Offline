from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol


PolishStreamCallback = Callable[[str], None | Awaitable[None]]


@dataclass(frozen=True, slots=True)
class PolishProviderConfig:
    """Connection and provider-specific settings for one polish backend."""

    name: str
    api_key: str
    base_url: str
    timeout_s: float
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PolishCompletionRequest:
    model: str
    messages: Sequence[Mapping[str, Any]]
    temperature: float | None = None
    max_output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class PolishCompletionResult:
    text: str | None
    status_code: int = 0


class PolishProvider(Protocol):
    async def complete(
        self,
        request: PolishCompletionRequest,
        *,
        on_delta: PolishStreamCallback | None = None,
        on_text: PolishStreamCallback | None = None,
    ) -> PolishCompletionResult: ...

    async def close(self) -> None: ...


async def invoke_callback(
    callback: PolishStreamCallback | None,
    value: str,
) -> None:
    if callback is None:
        return
    try:
        result = callback(value)
        if inspect.isawaitable(result):
            await result
    except Exception:
        # UI progress callbacks must never make the provider request fail.
        pass
