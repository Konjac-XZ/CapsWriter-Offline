"""Persistent per-day character statistics for text successfully sent to apps."""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import date
from pathlib import Path
from typing import Protocol

from src.infra.runtime_logging import application_data_directory
from src.infra import state_db


STATE_DIRECTORY_NAME = "State"
STATE_FILE_NAME = "daily_input.json"


class InfoLogger(Protocol):
    def info(self, message: str, *args: object) -> None: ...


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


def _migrate_legacy_daily_input() -> None:
    if state_db.migration_completed("daily_input_json"):
        return
    path = state_file_path()
    state = _read_state(path)
    day = state.get("date")
    if isinstance(day, str):
        try:
            character_count = max(0, int(state.get("character_count", 0)))
            last_logged_count = max(0, int(state.get("last_logged_count", 0)))
        except (TypeError, ValueError):
            character_count = 0
            last_logged_count = 0
        with state_db.connection() as database:
            database.execute(
                "INSERT OR IGNORE INTO daily_input_stats"
                "(day, character_count, last_logged_count, updated_at) "
                "VALUES(?, ?, ?, ?)",
                (day, character_count, last_logged_count, time.time()),
            )
            database.commit()
    state_db.mark_migration_completed("daily_input_json")
    if path.exists() and isinstance(day, str):
        backup = path.with_suffix(f"{path.suffix}.migrated.bak")
        try:
            if not backup.exists():
                path.replace(backup)
        except OSError:
            pass


def get_today_input_count(*, today: date | None = None) -> int:
    """Return today's count, treating missing/corrupt state as zero."""
    current_day = (today or date.today()).isoformat()
    try:
        _migrate_legacy_daily_input()
        with state_db.connection() as database:
            row = database.execute(
                "SELECT character_count FROM daily_input_stats WHERE day = ?",
                (current_day,),
            ).fetchone()
        return int(row["character_count"]) if row is not None else 0
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return 0


def record_input_characters(
    text: str,
    *,
    log_interval: int = 1000,
    today: date | None = None,
    logger: InfoLogger | None = None,
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
    try:
        _migrate_legacy_daily_input()
        interval = max(1, int(log_interval))
        with state_db.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            row = database.execute(
                "SELECT character_count, last_logged_count FROM daily_input_stats "
                "WHERE day = ?",
                (current_date.isoformat(),),
            ).fetchone()
            previous_count = int(row["character_count"]) if row is not None else 0
            previous_logged_count = (
                int(row["last_logged_count"]) if row is not None else 0
            )
            count = previous_count + added_count
            last_logged_count = (count // interval) * interval
            database.execute(
                "INSERT INTO daily_input_stats"
                "(day, character_count, last_logged_count, updated_at) "
                "VALUES(?, ?, ?, ?) ON CONFLICT(day) DO UPDATE SET "
                "character_count=excluded.character_count, "
                "last_logged_count=excluded.last_logged_count, "
                "updated_at=excluded.updated_at",
                (
                    current_date.isoformat(),
                    count,
                    max(previous_logged_count, last_logged_count),
                    time.time(),
                ),
            )
            database.commit()
    except (OSError, sqlite3.Error, ValueError, TypeError):
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
