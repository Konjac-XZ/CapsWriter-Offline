import os
from pathlib import Path

from src.polish.llm_polish import _build_messages
from src.polish.session_constraint import (
    MAX_SESSION_CONSTRAINT_CHARS,
    SESSION_CONSTRAINT_PATH_ENV,
    read_session_constraint,
    remove_session_constraint_file,
    write_session_constraint,
)


def test_session_constraint_round_trip_and_cleanup(monkeypatch, tmp_path: Path):
    path = tmp_path / "constraint.txt"
    monkeypatch.setenv(SESSION_CONSTRAINT_PATH_ENV, str(path))

    write_session_constraint(path, "  保持简短，并保留英文术语。  ")

    assert read_session_constraint() == "保持简短，并保留英文术语。"
    remove_session_constraint_file(path)
    assert not path.exists()
    assert SESSION_CONSTRAINT_PATH_ENV not in os.environ


def test_session_constraint_is_bounded(monkeypatch, tmp_path: Path):
    path = tmp_path / "constraint.txt"
    monkeypatch.setenv(SESSION_CONSTRAINT_PATH_ENV, str(path))

    write_session_constraint(path, "x" * (MAX_SESSION_CONSTRAINT_CHARS + 20))

    assert read_session_constraint() == "x" * MAX_SESSION_CONSTRAINT_CHARS


def test_missing_session_constraint_is_empty(monkeypatch, tmp_path: Path):
    monkeypatch.setenv(
        SESSION_CONSTRAINT_PATH_ENV,
        str(tmp_path / "missing.txt"),
    )

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
        "# 当前会话临时约束\n\n"
        "以下约束只适用于当前工作会话；若与上面的一般写作偏好冲突，"
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
