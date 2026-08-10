from __future__ import annotations

import logging

from src.polish.context_settings import (
    get_active_textbox_state_enabled,
    get_history_context_enabled,
    get_textbox_context_enabled,
    set_active_textbox_state_enabled,
    set_history_context_enabled,
    set_textbox_context_enabled,
)
from src.polish.llm_polish import (
    get_polish_prompt_text,
    is_llm_polish_enabled,
    update_polish_prompt_text,
)
from src.polish.vision_context import (
    is_vision_context_enabled,
    set_vision_context_enabled,
)
from src.provider.provider_config import provider_manager

from .lexicon import get_lexicon_editor_text


_LOGGER = logging.getLogger("capswriter.gui_api.configuration")


def get_configuration_snapshot() -> dict[str, object]:
    active = provider_manager.get_active_model()
    provider_id = active.ref.provider_id if active is not None else None
    return {
        "provider_id": provider_id,
        "provider_name": active.provider_name if active is not None else None,
        "asr_prompt": provider_manager.get_provider_prompt() if active else "",
        "llm_prompt": get_polish_prompt_text(),
        "llm_enabled": is_llm_polish_enabled(),
        "history_context_enabled": get_history_context_enabled(default=False),
        "textbox_context_enabled": get_textbox_context_enabled(default=False),
        "active_textbox_state_enabled": get_active_textbox_state_enabled(default=False),
        "vision_context_enabled": is_vision_context_enabled(),
        "lexicon_text": get_lexicon_editor_text(),
    }


def update_asr_prompt(provider_id: str, text: str) -> dict[str, object]:
    provider_id = provider_id.strip()
    if not provider_id:
        raise ValueError("provider_id is required")
    if not provider_manager.update_provider_prompt(
        provider_id,
        prompt=text if text.strip() else None,
    ):
        raise OSError("failed to persist the ASR prompt")
    return {"provider_id": provider_id, "apply_mode": "next_recording"}


def update_llm_prompt(text: str) -> dict[str, object]:
    if not update_polish_prompt_text(text):
        raise OSError("failed to persist the LLM prompt")
    return {"apply_mode": "immediate"}


def update_context_setting(name: str, enabled: bool) -> dict[str, object]:
    settings = {
        "history": (
            set_history_context_enabled,
            lambda: get_history_context_enabled(default=False),
        ),
        "textbox": (
            set_textbox_context_enabled,
            lambda: get_textbox_context_enabled(default=False),
        ),
        "textbox_state": (
            set_active_textbox_state_enabled,
            lambda: get_active_textbox_state_enabled(default=False),
        ),
        "vision": (set_vision_context_enabled, is_vision_context_enabled),
    }
    setting = settings.get(name)
    if setting is None:
        _LOGGER.warning("Context setting rejected name=%s reason=unsupported", name)
        raise ValueError(f"unsupported context setting: {name}")
    setter, getter = setting
    _LOGGER.info("Context setting update requested name=%s enabled=%s", name, enabled)
    if not setter(enabled):
        _LOGGER.error(
            "Context setting update failed name=%s enabled=%s stage=write",
            name,
            enabled,
        )
        raise OSError(f"failed to persist context setting: {name}")
    persisted = bool(getter())
    if persisted != enabled:
        _LOGGER.error(
            "Context setting update failed name=%s requested=%s persisted=%s stage=readback",
            name,
            enabled,
            persisted,
        )
        raise OSError(f"context setting readback mismatch: {name}")
    _LOGGER.info(
        "Context setting update confirmed name=%s enabled=%s",
        name,
        persisted,
    )
    return {"name": name, "enabled": persisted, "apply_mode": "immediate"}
