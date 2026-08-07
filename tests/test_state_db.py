from pathlib import Path

from src.infra import state_db


def test_app_state_round_trip_and_schema_initialization(monkeypatch, tmp_path: Path):
    path = tmp_path / "State" / "capswriter.db"
    monkeypatch.setattr(state_db, "database_path", lambda: path)

    state_db.set_app_state("example", "中文值")

    assert state_db.get_app_state("example") == "中文值"
    with state_db.connection() as database:
        assert database.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        assert database.execute(
            "SELECT value FROM schema_meta WHERE key='schema_version'"
        ).fetchone()["value"] == str(state_db.SCHEMA_VERSION)


def test_migration_marker_is_persistent(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(state_db, "database_path", lambda: tmp_path / "state.db")

    assert state_db.migration_completed("legacy") is False
    state_db.mark_migration_completed("legacy")
    assert state_db.migration_completed("legacy") is True
