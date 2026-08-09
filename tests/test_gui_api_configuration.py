from pathlib import Path

import yaml

from src.gui_api import lexicon
from src.polish import vision_context


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
