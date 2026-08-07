from __future__ import annotations

import os
import tempfile
from pathlib import Path


SESSION_CONSTRAINT_PATH_ENV = "CAPSWRITER_SESSION_CONSTRAINT_PATH"
MAX_SESSION_CONSTRAINT_CHARS = 8000


def create_session_constraint_file() -> Path:
    """Create an empty constraint file owned by the current GUI session."""
    file_descriptor, raw_path = tempfile.mkstemp(
        prefix="capswriter-session-constraint-",
        suffix=".txt",
    )
    os.close(file_descriptor)
    path = Path(raw_path)
    os.environ[SESSION_CONSTRAINT_PATH_ENV] = str(path)
    return path


def write_session_constraint(path: Path, text: str) -> None:
    """Atomically publish a constraint update for worker processes."""
    normalized = text[:MAX_SESSION_CONSTRAINT_CHARS]
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(normalized, encoding="utf-8")
    temporary_path.replace(path)


def read_session_constraint() -> str:
    """Read the current GUI-session constraint, if one is available."""
    raw_path = os.getenv(SESSION_CONSTRAINT_PATH_ENV, "").strip()
    if not raw_path:
        return ""
    try:
        return (
            Path(raw_path)
            .read_text(encoding="utf-8")[:MAX_SESSION_CONSTRAINT_CHARS]
            .strip()
        )
    except (OSError, UnicodeError):
        return ""


def remove_session_constraint_file(path: Path | None) -> None:
    """Remove the current session file without affecting another GUI session."""
    if path is None:
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    if os.getenv(SESSION_CONSTRAINT_PATH_ENV) == str(path):
        os.environ.pop(SESSION_CONSTRAINT_PATH_ENV, None)
