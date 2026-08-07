from pathlib import Path

import pytest

from src.infra import state_db
from src.polish.llm_polish import _build_messages
from src.polish.session_constraint import (
    MAX_SESSION_CONSTRAINT_CHARS,
    read_session_constraint,
    write_session_constraint,
)


@pytest.fixture(autouse=True)
def isolate_state_database(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(state_db, "database_path", lambda: tmp_path / "state.db")


def test_session_constraint_round_trip_and_survives_reopen():
    write_session_constraint("  保持简短，并保留英文术语。  ")

    assert read_session_constraint() == "保持简短，并保留英文术语。"
    assert read_session_constraint() == "保持简短，并保留英文术语。"


def test_session_constraint_is_bounded():
    write_session_constraint("x" * (MAX_SESSION_CONSTRAINT_CHARS + 20))

    assert read_session_constraint() == "x" * MAX_SESSION_CONSTRAINT_CHARS


def test_missing_session_constraint_is_empty():
    assert read_session_constraint() == ""


def test_constraint_is_an_explicit_system_prompt_section():
    messages = _build_messages(
        "长期规则",
        "ASR 原文",
        None,
        None,
        session_constraint="只输出一句话",
    )

    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == (
        "长期规则\n\n"
        "# 当前任务约束\n\n"
        "以下约束适用于当前任务；若与上面的一般写作偏好冲突，"
        "以本节为准：\n\n"
        "只输出一句话"
    )
    assert messages[-1]["content"].endswith("ASR 原文")


def test_constraint_works_without_a_persistent_system_prompt():
    messages = _build_messages(
        "",
        "ASR",
        None,
        None,
        session_constraint="保留 Markdown",
    )

    assert messages[0]["role"] == "system"
    assert "保留 Markdown" in messages[0]["content"]
