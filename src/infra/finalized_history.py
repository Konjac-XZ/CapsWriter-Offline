"""Durable storage for the recent text successfully sent to applications."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from src.infra.runtime_logging import application_data_directory


STATE_DIRECTORY_NAME = "State"
STATE_FILE_NAME = "finalized_history.json"
STATE_VERSION = 1
MAX_STORED_ITEMS = 100


def history_file_path() -> Path:
    """Return the per-user history path without creating it."""
    return application_data_directory() / STATE_DIRECTORY_NAME / STATE_FILE_NAME


def load_finalized_history() -> list[str]:
    """Load a bounded history snapshot, treating invalid state as empty."""
    try:
        payload: Any = json.loads(history_file_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    if not isinstance(payload, dict) or payload.get("version") != STATE_VERSION:
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    normalized = [
        item.strip() for item in items if isinstance(item, str) and item.strip()
    ]
    return normalized[-MAX_STORED_ITEMS:]


def save_finalized_history(items: list[str]) -> bool:
    """Atomically persist a bounded history snapshot.

    Persistence is best-effort: recognition and text output must keep working if
    the per-user state directory is temporarily unavailable.
    """
    path = history_file_path()
    payload = {
        "version": STATE_VERSION,
        "items": items[-MAX_STORED_ITEMS:],
    }
    temporary_path: str | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_path = tempfile.mkstemp(
            prefix=f".{path.stem}.", suffix=".tmp", dir=path.parent, text=True
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        Path(temporary_path).replace(path)
        return True
    except (OSError, TypeError, ValueError):
        if temporary_path is not None:
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except OSError:
                pass
        return False
