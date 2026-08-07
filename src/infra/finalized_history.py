"""Durable storage for the recent text successfully sent to applications."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence

from src.infra.runtime_logging import application_data_directory
from src.infra import state_db


STATE_DIRECTORY_NAME = "State"
STATE_FILE_NAME = "finalized_history.json"
STATE_VERSION = 2
MAX_STORED_ITEMS = 100


def history_file_path() -> Path:
    """Return the per-user history path without creating it."""
    return application_data_directory() / STATE_DIRECTORY_NAME / STATE_FILE_NAME


@dataclass(slots=True)
class FinalizedHistoryItem:
    original_text: str
    current_text: str
    session_id: str | None = None
    tracking_status: str = "unchanged"
    committed_at: float = 0.0
    modified_at: float | None = None


def _safe_timestamp(value: object) -> float:
    if not isinstance(value, (int, float, str)):
        return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def load_finalized_history() -> list[FinalizedHistoryItem]:
    """Load a bounded SQLite history snapshot after one-time JSON migration."""
    try:
        _migrate_legacy_history()
        with state_db.connection() as database:
            rows = database.execute(
                "SELECT session_id, original_text, current_text, tracking_status, "
                "committed_at, modified_at FROM finalized_history "
                "ORDER BY sequence DESC LIMIT ?",
                (MAX_STORED_ITEMS,),
            ).fetchall()
    except (OSError, sqlite3.Error):
        return []
    return [
        FinalizedHistoryItem(
            original_text=row["original_text"],
            current_text=row["current_text"],
            session_id=row["session_id"],
            tracking_status=row["tracking_status"],
            committed_at=row["committed_at"],
            modified_at=row["modified_at"],
        )
        for row in reversed(rows)
    ]


def _load_legacy_items() -> list[FinalizedHistoryItem]:
    try:
        payload: Any = json.loads(history_file_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, dict) or payload.get("version") not in {
        1,
        STATE_VERSION,
    }:
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    normalized: list[FinalizedHistoryItem] = []
    for item in items:
        if isinstance(item, str) and item.strip():
            text = item.strip()
            normalized.append(FinalizedHistoryItem(text, text))
            continue
        if not isinstance(item, dict):
            continue
        original = item.get("original_text")
        current = item.get("current_text")
        if not isinstance(original, str) or not original.strip():
            continue
        if not isinstance(current, str):
            continue
        normalized.append(
            FinalizedHistoryItem(
                original_text=original.strip(),
                current_text=current.strip(),
                session_id=(
                    item.get("session_id")
                    if isinstance(item.get("session_id"), str)
                    else None
                ),
                tracking_status=(
                    item.get("tracking_status")
                    if item.get("tracking_status") in {"unchanged", "edited", "deleted"}
                    else "unchanged"
                ),
                committed_at=_safe_timestamp(item.get("committed_at")),
                modified_at=(
                    float(item["modified_at"])
                    if isinstance(item.get("modified_at"), (int, float))
                    else None
                ),
            )
        )
    return normalized[-MAX_STORED_ITEMS:]


def _migrate_legacy_history() -> None:
    if state_db.migration_completed("finalized_history_json"):
        return
    legacy_path = history_file_path()
    items = _load_legacy_items()
    if items:
        _replace_history(items)
    state_db.mark_migration_completed("finalized_history_json")
    if legacy_path.exists() and items:
        backup = legacy_path.with_suffix(f"{legacy_path.suffix}.migrated.bak")
        try:
            if not backup.exists():
                legacy_path.replace(backup)
        except OSError:
            pass


def save_finalized_history(items: Sequence[FinalizedHistoryItem | str]) -> bool:
    """Atomically persist a bounded history snapshot.

    Persistence is best-effort: recognition and text output must keep working if
    the per-user state directory is temporarily unavailable.
    """
    normalized_items = [
        item
        if isinstance(item, FinalizedHistoryItem)
        else FinalizedHistoryItem(str(item).strip(), str(item).strip())
        for item in items
        if isinstance(item, FinalizedHistoryItem) or str(item).strip()
    ]
    try:
        _replace_history(normalized_items[-MAX_STORED_ITEMS:])
        return True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return False


def _replace_history(items: Sequence[FinalizedHistoryItem]) -> None:
    with state_db.connection() as database:
        database.execute("BEGIN IMMEDIATE")
        database.execute("DELETE FROM finalized_history")
        database.executemany(
            "INSERT INTO finalized_history(session_id, original_text, current_text, "
            "tracking_status, committed_at, modified_at) VALUES(?, ?, ?, ?, ?, ?)",
            [
                (
                    item.session_id,
                    item.original_text,
                    item.current_text,
                    item.tracking_status,
                    item.committed_at,
                    item.modified_at,
                )
                for item in items
            ],
        )
        database.commit()
