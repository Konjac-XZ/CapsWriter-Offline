from __future__ import annotations

import asyncio
from typing import Any

from src.polish.providers.base import PolishProvider, PolishProviderConfig
from src.polish.providers.openai_compatible import OpenAICompatiblePolishProvider
from src.polish.providers.openrouter import OpenRouterPolishProvider


_PROVIDER: PolishProvider | None = None
_PROVIDER_KEY: tuple[Any, ...] | None = None


def normalize_provider_name(value: object) -> str:
    name = str(value or "openai_compatible").strip().lower().replace("-", "_")
    aliases = {
        "openai": "openai_compatible",
        "openai_compat": "openai_compatible",
        "openrouter_sdk": "openrouter",
    }
    return aliases.get(name, name)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return tuple(sorted((str(key), _freeze(item)) for key, item in value.items()))
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


async def get_polish_provider(config: PolishProviderConfig) -> PolishProvider:
    global _PROVIDER, _PROVIDER_KEY

    loop_id = id(asyncio.get_running_loop())
    key = (
        normalize_provider_name(config.name),
        config.api_key,
        config.base_url.rstrip("/"),
        float(config.timeout_s),
        _freeze(dict(config.options)),
        loop_id,
    )
    reuse_provider = bool(config.options.get("reuse_client", True))
    if reuse_provider and _PROVIDER is not None and _PROVIDER_KEY == key:
        return _PROVIDER
    await close_polish_provider()

    provider_name = normalize_provider_name(config.name)
    if provider_name == "openrouter":
        provider: PolishProvider = OpenRouterPolishProvider(config)
    elif provider_name == "openai_compatible":
        provider = OpenAICompatiblePolishProvider(config)
    else:
        raise ValueError(f"Unsupported LLM polish provider: {config.name}")
    if reuse_provider:
        _PROVIDER = provider
        _PROVIDER_KEY = key
    return provider


async def close_polish_provider() -> None:
    global _PROVIDER, _PROVIDER_KEY

    provider = _PROVIDER
    _PROVIDER = None
    _PROVIDER_KEY = None
    if provider is not None:
        await provider.close()


__all__ = [
    "PolishProviderConfig",
    "close_polish_provider",
    "get_polish_provider",
    "normalize_provider_name",
]
