from pathlib import Path
import logging

import pytest
import yaml

from src.gui_api import configuration, lexicon
from src.polish import context_settings, llm_polish, vision_context


def test_update_lexicon_editor_text_persists_one_entry_per_line(
    monkeypatch,
    tmp_path: Path,
):
    target = tmp_path / "user_lexicon.yaml"
    monkeypatch.setattr(lexicon, "_lexicon_path", lambda: target)

    result = lexicon.update_lexicon_editor_text("CVE\n\n  MQTT  \n中文术语\n")

    assert result == {"entry_count": 3, "apply_mode": "next_recording"}
    assert yaml.safe_load(target.read_text(encoding="utf-8")) == {
        "words": ["CVE", "MQTT", "中文术语"]
    }


def test_set_vision_context_enabled_preserves_other_settings(
    monkeypatch,
    tmp_path: Path,
):
    config_path = tmp_path / "config" / "polish" / "vision.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "# comment\nenabled: false\ninterval_seconds: 300\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(vision_context, "_get_root_dir", lambda: tmp_path)

    assert vision_context.set_vision_context_enabled(True) is True
    assert config_path.read_text(encoding="utf-8") == (
        "# comment\nenabled: true\ninterval_seconds: 300\n"
    )


def test_native_gui_can_toggle_active_textbox_state_context(
    monkeypatch,
    tmp_path: Path,
):
    config_path = tmp_path / "polish.yaml"
    config_path.write_text(
        "active_textbox_state:\n  enabled: false\n  debug: false\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(context_settings, "polish_config_path", lambda: config_path)
    monkeypatch.setattr(llm_polish, "reload_polish_config", lambda: None)

    result = configuration.update_context_setting("textbox_state", True)

    assert result == {
        "name": "textbox_state",
        "enabled": True,
        "apply_mode": "immediate",
    }
    assert context_settings.get_active_textbox_state_enabled() is True
    assert config_path.read_text(encoding="utf-8") == (
        "active_textbox_state:\n  enabled: true\n  debug: false\n"
    )


def test_native_gui_adds_active_textbox_state_setting_to_older_config(
    monkeypatch,
    tmp_path: Path,
):
    config_path = tmp_path / "polish.yaml"
    config_path.write_text("history:\n  enabled: true\n", encoding="utf-8")
    monkeypatch.setattr(context_settings, "polish_config_path", lambda: config_path)
    monkeypatch.setattr(llm_polish, "reload_polish_config", lambda: None)

    result = configuration.update_context_setting("textbox_state", True)

    assert result["enabled"] is True
    assert config_path.read_text(encoding="utf-8") == (
        "history:\n  enabled: true\n\nactive_textbox_state:\n  enabled: true\n"
    )


def test_context_setting_rejects_readback_mismatch(monkeypatch, caplog):
    monkeypatch.setattr(
        configuration,
        "set_active_textbox_state_enabled",
        lambda _enabled: True,
    )
    monkeypatch.setattr(
        configuration,
        "get_active_textbox_state_enabled",
        lambda **_kwargs: False,
    )
    caplog.set_level(logging.INFO, logger="capswriter.gui_api.configuration")

    with pytest.raises(OSError, match="readback mismatch"):
        configuration.update_context_setting("textbox_state", True)

    assert "stage=readback" in caplog.text
    assert "requested=True persisted=False" in caplog.text
