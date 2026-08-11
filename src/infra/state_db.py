"""Shared SQLite storage for durable application state."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.infra.runtime_logging import application_data_directory


STATE_DIRECTORY_NAME = "State"
DATABASE_FILE_NAME = "capswriter.db"
SCHEMA_VERSION = 3
_schema_lock = threading.Lock()
_initialized_paths: set[Path] = set()


def database_path() -> Path:
    return application_data_directory() / STATE_DIRECTORY_NAME / DATABASE_FILE_NAME


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    database = sqlite3.connect(path, timeout=3.0)
    database.row_factory = sqlite3.Row
    try:
        database.execute("PRAGMA busy_timeout=3000")
        database.execute("PRAGMA foreign_keys=ON")
        _initialize(database, path)
        yield database
    finally:
        database.close()


def _initialize(database: sqlite3.Connection, path: Path) -> None:
    resolved = path.resolve()
    if resolved in _initialized_paths:
        return
    with _schema_lock:
        if resolved in _initialized_paths:
            return
        database.execute("PRAGMA journal_mode=WAL")
        database.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS app_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS finalized_history (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT,
                asr_text TEXT NOT NULL DEFAULT '',
                original_text TEXT NOT NULL,
                current_text TEXT NOT NULL,
                tracking_status TEXT NOT NULL,
                committed_at REAL NOT NULL,
                modified_at REAL
            );
            CREATE INDEX IF NOT EXISTS finalized_history_session
                ON finalized_history(session_id);
            CREATE TABLE IF NOT EXISTS daily_input_stats (
                day TEXT PRIMARY KEY,
                character_count INTEGER NOT NULL,
                last_logged_count INTEGER NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS correction_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL UNIQUE,
                asr_text TEXT NOT NULL DEFAULT '',
                committed_text TEXT NOT NULL,
                corrected_text TEXT NOT NULL,
                event_revision INTEGER NOT NULL DEFAULT 1,
                processed_revision INTEGER NOT NULL DEFAULT 0,
                first_observed_at REAL NOT NULL,
                last_observed_at REAL NOT NULL,
                attempt_count INTEGER NOT NULL DEFAULT 0,
                next_attempt_at REAL NOT NULL DEFAULT 0,
                lease_until REAL NOT NULL DEFAULT 0,
                last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS correction_events_due
                ON correction_events(
                    processed_revision,
                    next_attempt_at,
                    lease_until,
                    last_observed_at
                );
            CREATE TABLE IF NOT EXISTS learned_preferences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_key TEXT NOT NULL UNIQUE,
                kind TEXT NOT NULL,
                preferred_value TEXT NOT NULL,
                avoid_values_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL,
                status TEXT NOT NULL,
                evidence_count INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                last_matched_at REAL,
                match_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS learned_preferences_status
                ON learned_preferences(status);
            CREATE TABLE IF NOT EXISTS preference_keywords (
                preference_id INTEGER NOT NULL,
                keyword TEXT NOT NULL,
                normalized_keyword TEXT NOT NULL,
                weight REAL NOT NULL DEFAULT 1,
                PRIMARY KEY(preference_id, normalized_keyword),
                FOREIGN KEY(preference_id)
                    REFERENCES learned_preferences(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS preference_keywords_normalized
                ON preference_keywords(normalized_keyword);
            CREATE TABLE IF NOT EXISTS preference_evidence (
                preference_id INTEGER NOT NULL,
                correction_event_id INTEGER NOT NULL,
                event_revision INTEGER NOT NULL,
                committed_text TEXT NOT NULL,
                corrected_text TEXT NOT NULL,
                observed_at REAL NOT NULL,
                PRIMARY KEY(
                    preference_id,
                    correction_event_id,
                    event_revision
                ),
                FOREIGN KEY(preference_id)
                    REFERENCES learned_preferences(id) ON DELETE CASCADE,
                FOREIGN KEY(correction_event_id)
                    REFERENCES correction_events(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS reflection_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at REAL NOT NULL,
                completed_at REAL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                event_count INTEGER NOT NULL,
                outcome TEXT NOT NULL,
                response_hash TEXT,
                error_type TEXT
            );
            """
        )
        _ensure_column(
            database,
            "finalized_history",
            "asr_text",
            "TEXT NOT NULL DEFAULT ''",
        )
        schema_version_row = database.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        previous_schema_version = (
            int(schema_version_row["value"]) if schema_version_row is not None else 0
        )
        if previous_schema_version < 3:
            _split_preference_line_break_values(database)
        database.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        database.commit()
        _initialized_paths.add(resolved)


def _split_preference_line_break_values(database: sqlite3.Connection) -> None:
    """Repair list values saved as one item by the WinUI CR line separator."""
    preference_rows = database.execute(
        "SELECT id, avoid_values_json FROM learned_preferences"
    ).fetchall()
    for row in preference_rows:
        try:
            values = json.loads(str(row["avoid_values_json"]))
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(values, list):
            continue
        repaired = _split_string_values(values)
        if repaired != values:
            database.execute(
                "UPDATE learned_preferences SET avoid_values_json = ? WHERE id = ?",
                (json.dumps(repaired, ensure_ascii=False), int(row["id"])),
            )

    keyword_rows = database.execute(
        "SELECT preference_id, keyword, normalized_keyword, weight "
        "FROM preference_keywords"
    ).fetchall()
    for row in keyword_rows:
        keyword = str(row["keyword"])
        repaired = _split_text_value(keyword)
        if repaired == [keyword]:
            continue
        database.execute(
            "DELETE FROM preference_keywords "
            "WHERE preference_id = ? AND normalized_keyword = ?",
            (int(row["preference_id"]), str(row["normalized_keyword"])),
        )
        database.executemany(
            "INSERT INTO preference_keywords("
            "preference_id, keyword, normalized_keyword, weight) "
            "VALUES(?, ?, ?, ?) "
            "ON CONFLICT(preference_id, normalized_keyword) DO UPDATE SET "
            "keyword = excluded.keyword, weight = MAX(weight, excluded.weight)",
            [
                (
                    int(row["preference_id"]),
                    value,
                    _normalize_preference_keyword(value),
                    float(row["weight"]),
                )
                for value in repaired
                if _normalize_preference_keyword(value)
            ],
        )


def _split_string_values(values: list[object]) -> list[object]:
    repaired: list[object] = []
    for value in values:
        if not isinstance(value, str):
            repaired.append(value)
            continue
        repaired.extend(_split_text_value(value))
    return repaired


def _split_text_value(value: str) -> list[str]:
    if not re.search(r"[\r\n]", value):
        return [value]
    return [part.strip() for part in re.split(r"[\r\n]+", value) if part.strip()]


def _normalize_preference_keyword(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _ensure_column(
    database: sqlite3.Connection,
    table: str,
    column: str,
    declaration: str,
) -> None:
    columns = {
        str(row["name"])
        for row in database.execute(f"PRAGMA table_info({table})").fetchall()
    }
    if column not in columns:
        database.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def get_app_state(key: str, default: str = "") -> str:
    with connection() as database:
        row = database.execute(
            "SELECT value FROM app_state WHERE key = ?", (key,)
        ).fetchone()
    return str(row["value"]) if row is not None else default


def set_app_state(key: str, value: str) -> None:
    with connection() as database:
        database.execute(
            "INSERT INTO app_state(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET "
            "value=excluded.value, updated_at=excluded.updated_at",
            (key, value, time.time()),
        )
        database.commit()


def migration_completed(name: str) -> bool:
    with connection() as database:
        row = database.execute(
            "SELECT value FROM schema_meta WHERE key = ?", (f"migration.{name}",)
        ).fetchone()
    return row is not None and row["value"] == "1"


def mark_migration_completed(name: str) -> None:
    with connection() as database:
        database.execute(
            "INSERT INTO schema_meta(key, value) VALUES(?, '1') "
            "ON CONFLICT(key) DO UPDATE SET value='1'",
            (f"migration.{name}",),
        )
        database.commit()
