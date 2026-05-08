from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml


def _get_root_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def polish_config_path() -> Path:
    return _get_root_dir() / "config" / "polish" / "polish.yaml"


def get_textbox_context_enabled(default: bool = False) -> bool:
    try:
        data = yaml.safe_load(polish_config_path().read_text(encoding="utf-8")) or {}
        textbox_context = data.get("textbox_context", {})
        if not isinstance(textbox_context, dict):
            return default
        return bool(textbox_context.get("enabled", default))
    except Exception:
        return default


def set_textbox_context_enabled(enabled: bool) -> bool:
    path = polish_config_path()
    try:
        original = path.read_text(encoding="utf-8")
    except Exception:
        return False

    replacement = "true" if enabled else "false"
    lines = original.splitlines(keepends=True)
    section_pattern = re.compile(r"^textbox_context\s*:\s*$")
    value_pattern = re.compile(
        r"^(?P<indent>\s+)(?P<prefix>enabled\s*:\s*)"
        r"(?P<value>true|false)(?P<suffix>\s*(#.*)?)$",
        re.IGNORECASE,
    )

    in_section = False
    for idx, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if not in_section:
            if section_pattern.match(stripped):
                in_section = True
            continue

        if stripped and not stripped.startswith((" ", "\t", "#")):
            break

        match = value_pattern.match(stripped)
        if not match:
            continue

        newline = line[len(stripped):]
        lines[idx] = (
            f"{match.group('indent')}{match.group('prefix')}{replacement}"
            f"{match.group('suffix')}{newline}"
        )
        try:
            path.write_text("".join(lines), encoding="utf-8")
            from src.polish.llm_polish import reload_polish_config

            reload_polish_config()
            return True
        except Exception:
            return False

    return False


def toggle_textbox_context_enabled() -> bool | None:
    current = get_textbox_context_enabled(default=False)
    next_enabled = not current
    if not set_textbox_context_enabled(next_enabled):
        return None
    return next_enabled
