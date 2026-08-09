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
    assert schema_version == str(state_db.SCHEMA_VERSION)


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


def test_empty_tsf_update_is_ignored(monkeypatch):
    monkeypatch.setattr(
        llm_polish,
        "_cfg",
        lambda: {
            "history": {"enabled": True, "max_size": 5},
            "personalization": {"enabled": True},
        },
    )
    monkeypatch.setattr(llm_polish, "_qwen_asr_history_settings", lambda: (False, 0))
    monkeypatch.setattr(llm_polish, "_finalized_history", [])
    monkeypatch.setattr(llm_polish, "_history_loaded", True)
    saved = []
    monkeypatch.setattr(
        llm_polish,
        "save_finalized_history",
        lambda items: saved.append(list(items)) or True,
    )
    corrections = []
    monkeypatch.setattr(
        "src.personalization.record_correction",
        lambda **kwargs: corrections.append(kwargs),
    )

    llm_polish.record_finalized_text("提交后被输入框清空的内容", "session-1")
    assert llm_polish.update_finalized_text("session-1", "  \n") is False

    item = llm_polish._finalized_history[0]
    assert item.current_text == "提交后被输入框清空的内容"
    assert item.tracking_status == "unchanged"
    assert llm_polish.get_finalized_history() == ["提交后被输入框清空的内容"]
    assert len(saved) == 1
    assert corrections == []


def test_legacy_deleted_history_item_is_omitted(monkeypatch):
    monkeypatch.setattr(
        llm_polish, "_cfg", lambda: {"history": {"enabled": True, "max_size": 5}}
    )
    monkeypatch.setattr(
        llm_polish,
        "_finalized_history",
        [
            finalized_history.FinalizedHistoryItem(
                original_text="旧记录",
                current_text="",
                tracking_status="deleted",
            )
        ],
    )
    monkeypatch.setattr(llm_polish, "_history_loaded", True)

    assert llm_polish.get_finalized_history() == []


def test_tsf_edit_survives_short_history_eviction_in_correction_journal(
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
        lambda: {
            "history": {"enabled": True, "max_size": 2},
            "personalization": {"enabled": True},
        },
    )
    monkeypatch.setattr(llm_polish, "_qwen_asr_history_settings", lambda: (False, 0))
    monkeypatch.setattr(llm_polish, "_finalized_history", [])
    monkeypatch.setattr(llm_polish, "_history_loaded", False)

    llm_polish.record_finalized_text(
        "Type Script",
        "session-1",
        asr_text="泰普斯克瑞普特",
    )
    assert llm_polish.update_finalized_text("session-1", "TypeScript")
    for index in range(25):
        llm_polish.record_finalized_text(f"后续 {index}")

    assert len(llm_polish.get_finalized_history()) == 2
    with state_db.connection() as database:
        correction = database.execute(
            "SELECT asr_text, committed_text, corrected_text FROM correction_events"
        ).fetchone()
    assert tuple(correction) == ("泰普斯克瑞普特", "Type Script", "TypeScript")


def test_correction_observed_before_history_binding_is_journaled(monkeypatch):
    class AlreadyEditedBridge:
        def get_tracked_text(self, session_id):
            assert session_id == "session-race"
            return "TypeScript"

    monkeypatch.setattr(
        "src.tsf_ipc.get_tsf_speech_tip_bridge",
        lambda: AlreadyEditedBridge(),
    )
    monkeypatch.setattr(
        llm_polish,
        "_cfg",
        lambda: {
            "history": {"enabled": True, "max_size": 20},
            "personalization": {"enabled": True},
        },
    )
    monkeypatch.setattr(llm_polish, "_qwen_asr_history_settings", lambda: (False, 0))
    monkeypatch.setattr(llm_polish, "_finalized_history", [])
    monkeypatch.setattr(llm_polish, "_history_loaded", False)

    llm_polish.record_finalized_text(
        "Type Script",
        "session-race",
        asr_text="泰普斯克瑞普特",
    )

    with state_db.connection() as database:
        correction = database.execute(
            "SELECT asr_text, committed_text, corrected_text FROM correction_events"
        ).fetchone()
    assert tuple(correction) == ("泰普斯克瑞普特", "Type Script", "TypeScript")
