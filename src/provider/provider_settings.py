"""Shared helpers to read provider settings in a DRY, YAML-first way.

Handlers should import these functions instead of duplicating logic.
"""

from __future__ import annotations

import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterable, Optional

from src.provider.domain import ResolvedModel

try:
    from src.provider.provider_config import provider_manager
except Exception:  # pragma: no cover
    provider_manager = None  # type: ignore


_resolved_model: ContextVar[ResolvedModel | None] = ContextVar(
    "resolved_transcription_model", default=None
)


@contextmanager
def use_resolved_model(model: ResolvedModel):
    """Bind immutable request settings while a provider adapter is executing."""
    token = _resolved_model.set(model)
    try:
        yield
    finally:
        _resolved_model.reset(token)


def get_resolved_model() -> ResolvedModel | None:
    return _resolved_model.get()


def _active_settings() -> dict[str, Any]:
    resolved = _resolved_model.get()
    if resolved is not None:
        return dict(resolved.settings)
    if provider_manager is None:  # pragma: no cover
        return {}
    try:
        return provider_manager.get_active_settings()
    except Exception:  # pragma: no cover
        return {}


def _first_env(env_names: Optional[Iterable[str]]) -> Optional[str]:
    if not env_names:
        return None
    for name in env_names:
        val = os.getenv(name)
        if val is not None:
            return val
    return None


_ENV_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _expand_env_refs(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    def repl(match: re.Match[str]) -> str:
        return os.getenv(match.group(1), "")

    return _ENV_REF_RE.sub(repl, value)


def get_value(
    key: str,
    env: str | Iterable[str] | None = None,
    default: Any = None,
    cast: Any | None = None,
) -> Any:
    """Return active setting by key with optional env fallback and casting."""
    settings = _active_settings()
    if key in settings:
        val = settings.get(key)
    else:
        env_names = [env] if isinstance(env, str) else list(env or [])
        val = _first_env(env_names)
    val = _expand_env_refs(val)
    if val is None:
        return default
    if cast is None:
        return val
    try:
        if cast is bool:
            if isinstance(val, bool):
                return val
            if isinstance(val, (int, float)):
                return bool(val)
            s = str(val).strip().lower()
            return s not in {"", "0", "false", "no"}
        if cast is float:
            return float(val)
        if cast is int:
            return int(val)
        if cast is str:
            return str(val)
        # Custom callable
        return cast(val)
    except Exception:  # pragma: no cover
        return default


def get_str(
    key: str, env: str | Iterable[str] | None = None, default: Optional[str] = None
) -> Optional[str]:
    val = get_value(key, env, default=None, cast=str)
    if val is None:
        return default
    s = str(val).strip()
    return s if s else default


def get_bool(
    key: str, env: str | Iterable[str] | None = None, default: bool = False
) -> bool:
    val = get_value(key, env, default=None, cast=bool)
    return default if val is None else bool(val)


def get_float(
    key: str, env: str | Iterable[str] | None = None, default: float | None = None
) -> float | None:
    val = get_value(key, env, default=None, cast=float)
    return default if val is None else float(val)


def get_int(
    key: str, env: str | Iterable[str] | None = None, default: int | None = None
) -> int | None:
    val = get_value(key, env, default=None, cast=int)
    return default if val is None else int(val)


def get_context_prompt() -> str:
    """Return free-form transcription context without user lexicon terms."""
    resolved = _resolved_model.get()
    if resolved is not None:
        settings = resolved.settings
        custom_prompt = settings.get("prompt")
        if isinstance(custom_prompt, str) and custom_prompt.strip():
            return custom_prompt

        preset_name = settings.get("prompt_preset")
        if isinstance(preset_name, str) and preset_name.strip():
            if provider_manager is not None:
                try:
                    preset = provider_manager.get_prompt_preset(preset_name)
                    if isinstance(preset, str) and preset.strip():
                        return preset
                except Exception:
                    pass

    if provider_manager is not None:
        try:
            prompt = provider_manager.get_provider_prompt()
            if isinstance(prompt, str) and prompt.strip():
                return prompt
        except Exception:
            pass
    return os.getenv("TRANSCRIBE_PROMPT", "")


def get_prompt() -> str:
    from src.infra.user_lexicon import (
        get_hot_word_block,
    )  # local import to avoid circular deps

    base = get_context_prompt()
    return base + get_hot_word_block() if base else base
