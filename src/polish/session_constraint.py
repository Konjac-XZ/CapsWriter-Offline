from __future__ import annotations

from src.infra.state_db import get_app_state, set_app_state


MAX_SESSION_CONSTRAINT_CHARS = 8000
SESSION_CONSTRAINT_STATE_KEY = "polish.current_task_constraint"


def write_session_constraint(text: str) -> None:
    """Persist the current task constraint for all application processes."""
    set_app_state(SESSION_CONSTRAINT_STATE_KEY, text[:MAX_SESSION_CONSTRAINT_CHARS])


def read_session_constraint() -> str:
    return get_app_state(SESSION_CONSTRAINT_STATE_KEY)[
        :MAX_SESSION_CONSTRAINT_CHARS
    ].strip()
