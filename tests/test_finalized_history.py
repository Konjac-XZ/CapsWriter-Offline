import json
from pathlib import Path

import pytest

from src.infra import finalized_history, state_db
from src.polish import llm_polish


@pytest.fixture(autouse=True)
def isolate_state_database(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(state_db, "database_path", lambda: tmp_path / "state.db")


def test_history_round_trip_is_bounded_and_versioned(monkeypatch, tmp_path: Path):
    state_path = tmp_path / "State" / "finalized_history.json"
    monkeypatch.setattr(finalized_history, "history_file_path", lambda: state_path)

    items = [f"第 {index} 条" for index in range(105)]

    assert finalized_history.save_finalized_history(items) is True
    loaded = finalized_history.load_finalized_history()
    assert [item.current_text for item in loaded] == items[-100:]
    with state_db.connection() as database:
        schema_version = database.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"]
    assert schema_version == "1"


def test_invalid_history_state_is_treated_as_empty(monkeypatch, tmp_path: Path):
    state_path = tmp_path / "finalized_history.json"
    state_path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(finalized_history, "history_file_path", lambda: state_path)

    assert finalized_history.load_finalized_history() == []


def test_history_loader_ignores_invalid_items(monkeypatch, tmp_path: Path):
    state_path = tmp_path / "finalized_history.json"
    state_path.write_text(
        json.dumps(
            {"version": 1, "items": [" 第一条 ", "", None, 42, "第二条"]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(finalized_history, "history_file_path", lambda: state_path)

    assert [
        item.current_text for item in finalized_history.load_finalized_history()
    ] == ["第一条", "第二条"]


def test_polish_history_survives_restart_and_clear_is_persistent(
    monkeypatch, tmp_path: Path
):
    state_path = tmp_path / "State" / "finalized_history.json"
    monkeypatch.setattr(finalized_history, "history_file_path", lambda: state_path)
    monkeypatch.setattr(
        llm_polish, "load_finalized_history", finalized_history.load_finalized_history
    )
    monkeypatch.setattr(
        llm_polish, "save_finalized_history", finalized_history.save_finalized_history
    )
    monkeypatch.setattr(
        llm_polish,
        "_cfg",
        lambda: {"history": {"enabled": True, "max_size": 2}},
    )
    monkeypatch.setattr(llm_polish, "_qwen_asr_history_settings", lambda: (False, 0))
    monkeypatch.setattr(llm_polish, "_finalized_history", [])
    monkeypatch.setattr(llm_polish, "_history_loaded", False)

    llm_polish.record_finalized_text("第一条")
    llm_polish.record_finalized_text("第二条")
    llm_polish.record_finalized_text("第三条")

    # Reset the process-local cache to simulate a fresh application process.
    llm_polish._finalized_history = []
    llm_polish._history_loaded = False
    assert llm_polish.get_finalized_history() == ["第二条", "第三条"]

    assert llm_polish.clear_finalized_history() == 2
    llm_polish._finalized_history = []
    llm_polish._history_loaded = False
    assert llm_polish.get_finalized_history() == []


def test_tsf_edit_updates_history_and_exposes_before_after(monkeypatch, tmp_path: Path):
    state_path = tmp_path / "State" / "finalized_history.json"
    monkeypatch.setattr(finalized_history, "history_file_path", lambda: state_path)
    monkeypatch.setattr(
        llm_polish, "load_finalized_history", finalized_history.load_finalized_history
    )
    monkeypatch.setattr(
        llm_polish, "save_finalized_history", finalized_history.save_finalized_history
    )
    monkeypatch.setattr(
        llm_polish, "_cfg", lambda: {"history": {"enabled": True, "max_size": 5}}
    )
    monkeypatch.setattr(llm_polish, "_qwen_asr_history_settings", lambda: (False, 0))
    monkeypatch.setattr(llm_polish, "_finalized_history", [])
    monkeypatch.setattr(llm_polish, "_history_loaded", False)

    llm_polish.record_finalized_text("我们使用 TypeScript 实现", "session-1")
    assert llm_polish.update_finalized_text("session-1", "我们使用 TSF 实现")
    assert llm_polish.get_finalized_history() == [
        "语音上屏：我们使用 TypeScript 实现\n用户改为：我们使用 TSF 实现"
    ]
