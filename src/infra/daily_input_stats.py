"""Persistent per-day character statistics for text successfully sent to apps."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import date
from pathlib import Path

from src.infra.runtime_logging import application_data_directory


STATE_DIRECTORY_NAME = "State"
STATE_FILE_NAME = "daily_input.json"


def state_file_path() -> Path:
    """Return the daily input state path without creating it."""
    return application_data_directory() / STATE_DIRECTORY_NAME / STATE_FILE_NAME


def _read_state(path: Path) -> dict[str, int | str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _state_for_today(path: Path, today: date) -> dict[str, int | str]:
    state = _read_state(path)
    if state.get("date") != today.isoformat():
        return {"date": today.isoformat(), "character_count": 0, "last_logged_count": 0}
    try:
        character_count = max(0, int(state.get("character_count", 0)))
        last_logged_count = max(0, int(state.get("last_logged_count", 0)))
    except (TypeError, ValueError):
        character_count = 0
        last_logged_count = 0
    return {
        "date": today.isoformat(),
        "character_count": character_count,
        "last_logged_count": last_logged_count,
    }


def _write_state(path: Path, state: dict[str, int | str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_path = tempfile.mkstemp(
        prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_path).replace(path)
    except Exception:
        try:
            Path(temporary_path).unlink(missing_ok=True)
        except OSError:
            pass
        raise


def get_today_input_count(*, today: date | None = None) -> int:
    """Return today's count, treating missing/corrupt state as zero."""
    path = state_file_path()
    state = _state_for_today(path, today or date.today())
    return int(state["character_count"])


def record_input_characters(
    text: str,
    *,
    log_interval: int = 1000,
    today: date | None = None,
    logger: logging.Logger | None = None,
) -> int:
    """Add successfully input characters and log each crossed interval.

    The counter intentionally uses Python character count: Chinese characters,
    Latin letters, spaces, and punctuation each count as one character because
    each was sent to the focused application.
    """
    added_count = len(text)
    if added_count == 0:
        return get_today_input_count(today=today)

    current_date = today or date.today()
    path = state_file_path()
    try:
        state = _state_for_today(path, current_date)
        count = int(state["character_count"]) + added_count
        previous_logged_count = int(state["last_logged_count"])
        interval = max(1, int(log_interval))
        last_logged_count = (count // interval) * interval
        state["character_count"] = count
        state["last_logged_count"] = max(previous_logged_count, last_logged_count)
        _write_state(path, state)
    except (OSError, ValueError, TypeError):
        return 0

    if last_logged_count > previous_logged_count:
        event_logger = logger or logging.getLogger("capswriter.usage")
        for milestone in range(
            ((previous_logged_count // interval) + 1) * interval,
            last_logged_count + 1,
            interval,
        ):
            event_logger.info("Daily input milestone reached: characters=%d", milestone)
    return count
