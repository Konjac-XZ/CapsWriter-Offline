"""Shared SQLite storage for durable application state."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from src.infra.runtime_logging import application_data_directory


STATE_DIRECTORY_NAME = "State"
DATABASE_FILE_NAME = "capswriter.db"
SCHEMA_VERSION = 1
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
            """
        )
        database.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(SCHEMA_VERSION),),
        )
        database.commit()
        _initialized_paths.add(resolved)


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
