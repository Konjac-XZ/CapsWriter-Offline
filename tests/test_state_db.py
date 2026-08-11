import json
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


def test_schema_upgrade_splits_preference_values_saved_with_cr(
    monkeypatch, tmp_path: Path
):
    path = tmp_path / "state.db"
    monkeypatch.setattr(state_db, "database_path", lambda: path)

    with state_db.connection() as database:
        cursor = database.execute(
            "INSERT INTO learned_preferences("
            "canonical_key, kind, preferred_value, avoid_values_json, confidence, "
            "status, evidence_count, created_at, updated_at) "
            "VALUES('legacy', 'terminology', 'dnsmasq', ?, 1, 'active', 1, 1, 1)",
            (json.dumps(["DNS mask\rDNSMask"], ensure_ascii=False),),
        )
        preference_id = int(cursor.lastrowid or 0)
        database.execute(
            "INSERT INTO preference_keywords("
            "preference_id, keyword, normalized_keyword, weight) "
            "VALUES(?, ?, ?, 1)",
            (preference_id, "DNS mask\rDNSMask", "dns mask dnsmask"),
        )
        database.execute(
            "UPDATE schema_meta SET value = '2' WHERE key = 'schema_version'"
        )
        database.commit()

    state_db._initialized_paths.discard(path.resolve())
    with state_db.connection() as database:
        avoid_values = json.loads(
            database.execute(
                "SELECT avoid_values_json FROM learned_preferences WHERE id = ?",
                (preference_id,),
            ).fetchone()[0]
        )
        keywords = [
            row[0]
            for row in database.execute(
                "SELECT keyword FROM preference_keywords "
                "WHERE preference_id = ? ORDER BY normalized_keyword",
                (preference_id,),
            ).fetchall()
        ]
        schema_version = database.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()[0]

    assert avoid_values == ["DNS mask", "DNSMask"]
    assert keywords == ["DNS mask", "DNSMask"]
    assert schema_version == str(state_db.SCHEMA_VERSION)
