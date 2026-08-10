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
class PolishUsage:
    """Provider-reported token usage for one polish completion attempt."""

    prompt_tokens: int | None = None
    cached_tokens: int | None = None
    cache_write_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class PolishCompletionResult:
    text: str | None
    status_code: int = 0
    usage: PolishUsage | None = None


def _value(obj: Any, key: str) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key)
    return getattr(obj, key, None)


def _nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    return None


def extract_polish_usage(payload: Any) -> PolishUsage | None:
    """Read OpenAI-compatible usage from mappings or SDK response objects."""

    usage = _value(payload, "usage")
    if usage is None:
        return None

    raw_prompt_tokens = _value(usage, "prompt_tokens")
    if raw_prompt_tokens is None:
        raw_prompt_tokens = _value(usage, "input_tokens")
    prompt_tokens = _nonnegative_int(raw_prompt_tokens)
    raw_completion_tokens = _value(usage, "completion_tokens")
    if raw_completion_tokens is None:
        raw_completion_tokens = _value(usage, "output_tokens")
    completion_tokens = _nonnegative_int(raw_completion_tokens)
    details = _value(usage, "prompt_tokens_details")
    if details is None:
        details = _value(usage, "input_tokens_details")
    cached_tokens = _nonnegative_int(_value(details, "cached_tokens"))
    cache_write_tokens = _nonnegative_int(_value(details, "cache_write_tokens"))

    if all(
        value is None
        for value in (
            prompt_tokens,
            cached_tokens,
            cache_write_tokens,
            completion_tokens,
        )
    ):
        return None
    return PolishUsage(
        prompt_tokens=prompt_tokens,
        cached_tokens=cached_tokens,
        cache_write_tokens=cache_write_tokens,
        completion_tokens=completion_tokens,
    )


def merge_polish_usage(
    previous: PolishUsage | None,
    current: PolishUsage | None,
) -> PolishUsage | None:
    """Merge partial streaming usage chunks, preferring newer known values."""

    if previous is None:
        return current
    if current is None:
        return previous
    return PolishUsage(
        prompt_tokens=(
            current.prompt_tokens
            if current.prompt_tokens is not None
            else previous.prompt_tokens
        ),
        cached_tokens=(
            current.cached_tokens
            if current.cached_tokens is not None
            else previous.cached_tokens
        ),
        cache_write_tokens=(
            current.cache_write_tokens
            if current.cache_write_tokens is not None
            else previous.cache_write_tokens
        ),
        completion_tokens=(
            current.completion_tokens
            if current.completion_tokens is not None
            else previous.completion_tokens
        ),
    )


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
