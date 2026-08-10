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


def _get_section_bool(section: str, key: str, default: bool) -> bool:
    try:
        data = yaml.safe_load(polish_config_path().read_text(encoding="utf-8")) or {}
        section_data = data.get(section, {})
        if not isinstance(section_data, dict):
            return default
        return bool(section_data.get(key, default))
    except Exception:
        return default


def _set_section_bool(section: str, key: str, enabled: bool) -> bool:
    path = polish_config_path()
    try:
        original = path.read_text(encoding="utf-8")
    except Exception:
        return False

    replacement = "true" if enabled else "false"
    lines = original.splitlines(keepends=True)
    section_pattern = re.compile(rf"^{re.escape(section)}\s*:\s*$")
    value_pattern = re.compile(
        rf"^(?P<indent>\s+)(?P<prefix>{re.escape(key)}\s*:\s*)"
        r"(?P<value>true|false)(?P<suffix>\s*(#.*)?)$",
        re.IGNORECASE,
    )

    in_section = False
    section_found = False
    insertion_index = len(lines)
    for idx, line in enumerate(lines):
        stripped = line.rstrip("\r\n")
        if not in_section:
            if section_pattern.match(stripped):
                in_section = True
                section_found = True
                insertion_index = idx + 1
            continue

        if stripped and not stripped.startswith((" ", "\t", "#")):
            insertion_index = idx
            break

        insertion_index = idx + 1

        match = value_pattern.match(stripped)
        if not match:
            continue

        newline = line[len(stripped) :]
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

    newline = "\r\n" if any(line.endswith("\r\n") for line in lines) else "\n"
    if section_found:
        lines.insert(insertion_index, f"  {key}: {replacement}{newline}")
    else:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += newline
        if lines and lines[-1].strip():
            lines.append(newline)
        lines.extend(
            [
                f"{section}:{newline}",
                f"  {key}: {replacement}{newline}",
            ]
        )

    try:
        path.write_text("".join(lines), encoding="utf-8")
        from src.polish.llm_polish import reload_polish_config

        reload_polish_config()
        return True
    except Exception:
        return False


def get_textbox_context_enabled(default: bool = False) -> bool:
    return _get_section_bool("textbox_context", "enabled", default)


def set_textbox_context_enabled(enabled: bool) -> bool:
    return _set_section_bool("textbox_context", "enabled", enabled)


def get_active_textbox_state_enabled(default: bool = False) -> bool:
    return _get_section_bool("active_textbox_state", "enabled", default)


def set_active_textbox_state_enabled(enabled: bool) -> bool:
    return _set_section_bool("active_textbox_state", "enabled", enabled)


def get_history_context_enabled(default: bool = False) -> bool:
    return _get_section_bool("history", "enabled", default)


def set_history_context_enabled(enabled: bool) -> bool:
    return _set_section_bool("history", "enabled", enabled)


def toggle_textbox_context_enabled() -> bool | None:
    current = get_textbox_context_enabled(default=False)
    next_enabled = not current
    if not set_textbox_context_enabled(next_enabled):
        return None
    return next_enabled
