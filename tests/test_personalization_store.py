import sqlite3
from pathlib import Path
from typing import cast

import pytest

from src.infra import state_db
from src.personalization.store import (
    PreferenceProposal,
    ReflectionOutcome,
    apply_reflection_outcomes,
    clear_personalization_data,
    create_learned_preference,
    delete_learned_preference,
    get_learned_preferences_snapshot,
    get_reflection_store_snapshot,
    lease_due_corrections,
    mark_correction_failure,
    record_correction,
    search_preferences,
    update_learned_preference,
)


@pytest.fixture(autouse=True)
def isolate_state_database(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(state_db, "database_path", lambda: tmp_path / "state.db")


def _proposal(*, kind: str = "terminology") -> PreferenceProposal:
    return PreferenceProposal(
        kind=kind,
        preferred_value="TypeScript",
        avoid_values=("Type Script",),
        keywords=("TypeScript", "Type Script", "前端"),
    )


def test_reflection_store_snapshot_reports_queue_without_text_content():
    assert record_correction(
        session_id="session-status",
        asr_text="sensitive original",
        committed_text="sensitive committed",
        corrected_text="sensitive corrected",
        observed_at=10,
    )

    snapshot = get_reflection_store_snapshot(now=20)

    assert snapshot["corrections"] == {
        "total": 1,
        "pending": 1,
        "due": 1,
        "leased": 0,
    }
    assert "sensitive" not in repr(snapshot)


def test_learned_preferences_snapshot_returns_displayable_details():
    record_correction(
        session_id="session-preference-list",
        asr_text="private raw text",
        committed_text="Type Script",
        corrected_text="TypeScript",
        observed_at=10,
    )
    events = lease_due_corrections(now=20, settle_seconds=0)
    outcome = ReflectionOutcome(
        event_id=events[0].id,
        event_revision=events[0].event_revision,
        classification="preference",
        proposal=_proposal(),
    )
    apply_reflection_outcomes(events, [outcome], now=25)

    snapshot = get_learned_preferences_snapshot()

    assert snapshot["total"] == 1
    item = cast(list[dict[str, object]], snapshot["items"])[0]
    assert item["preferred_value"] == "TypeScript"
    assert item["avoid_values"] == ["Type Script"]
    assert item["status"] == "active"
    assert item["evidence_count"] == 1
    assert item["keywords"] == ["Type Script"]
    assert "private raw text" not in repr(snapshot)


def test_manual_preference_can_be_created_and_retrieved():
    preference_id = create_learned_preference(
        kind="terminology",
        preferred_value="CapsWriter",
        avoid_values=("Caps Writer",),
        keywords=("Caps Writer",),
        status="active",
    )

    snapshot = get_learned_preferences_snapshot()
    item = cast(list[dict[str, object]], snapshot["items"])[0]
    assert item["id"] == preference_id
    assert item["preferred_value"] == "CapsWriter"
    assert item["evidence_count"] == 0
    assert item["keywords"] == ["Caps Writer"]

    with pytest.raises(ValueError, match="already uses this value"):
        create_learned_preference(
            kind="terminology",
            preferred_value="CapsWriter",
            avoid_values=("Caps Writer",),
            keywords=(),
            status="active",
        )


def test_correction_is_durable_and_multiple_edits_coalesce():
    assert record_correction(
        session_id="session-1",
        asr_text="Type Script",
        committed_text="Type Script",
        corrected_text="TypeScript",
        observed_at=10,
    )
    assert record_correction(
        session_id="session-1",
        asr_text="Type Script",
        committed_text="Type Script",
        corrected_text="TypeScript 代码",
        observed_at=20,
    )

    events = lease_due_corrections(
        now=30,
        settle_seconds=0,
        batch_size=5,
        lease_seconds=60,
    )

    assert len(events) == 1
    assert events[0].event_revision == 2
    assert events[0].corrected_text == "TypeScript 代码"
    with state_db.connection() as database:
        assert (
            database.execute("SELECT COUNT(*) FROM correction_events").fetchone()[0]
            == 1
        )


def test_reverting_to_committed_text_acknowledges_latest_revision():
    record_correction(
        session_id="session-1",
        asr_text="原文",
        committed_text="原文",
        corrected_text="修改",
        observed_at=10,
    )
    record_correction(
        session_id="session-1",
        asr_text="原文",
        committed_text="原文",
        corrected_text="原文",
        observed_at=20,
    )

    assert lease_due_corrections(now=30, settle_seconds=0) == []
    with state_db.connection() as database:
        row = database.execute(
            "SELECT event_revision, processed_revision FROM correction_events"
        ).fetchone()
    assert tuple(row) == (2, 2)


def test_reflection_upsert_activates_and_retrieves_scoped_preference():
    record_correction(
        session_id="session-1",
        asr_text="Type Script",
        committed_text="Type Script",
        corrected_text="TypeScript",
        observed_at=10,
    )
    events = lease_due_corrections(now=20, settle_seconds=0)
    outcome = ReflectionOutcome(
        event_id=events[0].id,
        event_revision=events[0].event_revision,
        classification="preference",
        proposal=_proposal(),
    )

    assert apply_reflection_outcomes(events, [outcome], now=25) == (1, 0)
    matches = search_preferences("我们用 Type Script 开发前端", now=30)

    assert len(matches) == 1
    assert matches[0].preferred_value == "TypeScript"
    assert "Type Script" in matches[0].matched_keywords
    with state_db.connection() as database:
        row = database.execute(
            "SELECT processed_revision, event_revision FROM correction_events"
        ).fetchone()
        preference = database.execute(
            "SELECT status, evidence_count, match_count FROM learned_preferences"
        ).fetchone()
    assert tuple(row) == (1, 1)
    assert tuple(preference) == ("active", 1, 1)


def test_broad_style_preference_requires_two_distinct_evidence_events():
    for index in (1, 2):
        record_correction(
            session_id=f"session-{index}",
            asr_text="原文",
            committed_text=f"较长表达 {index}",
            corrected_text=f"简洁表达 {index}",
            observed_at=float(index),
        )
    first, second = lease_due_corrections(
        now=10,
        settle_seconds=0,
        batch_size=2,
    )
    style = PreferenceProposal(
        kind="style",
        preferred_value="使用简洁表达",
        avoid_values=(),
        keywords=("表达", "说明"),
    )

    apply_reflection_outcomes(
        [first],
        [
            ReflectionOutcome(
                first.id,
                first.event_revision,
                "preference",
                style,
            )
        ],
        now=11,
    )
    assert search_preferences("请说明这个表达", now=12) == []

    apply_reflection_outcomes(
        [second],
        [
            ReflectionOutcome(
                second.id,
                second.event_revision,
                "preference",
                style,
            )
        ],
        now=13,
    )
    assert len(search_preferences("请说明这个表达", now=14)) == 1


def test_conflicting_preference_stays_candidate_until_repeated_then_supersedes():
    proposals = (
        PreferenceProposal(
            "terminology",
            "TypeScript",
            ("TSF",),
            ("TypeScript", "TSF"),
        ),
        PreferenceProposal(
            "terminology",
            "TSF",
            ("TypeScript",),
            ("TypeScript", "TSF"),
        ),
    )

    def reflect(session: str, proposal: PreferenceProposal, observed_at: float) -> None:
        record_correction(
            session_id=session,
            asr_text="术语",
            committed_text=f"请使用 {proposal.avoid_values[0]}",
            corrected_text=f"请使用 {proposal.preferred_value}",
            observed_at=observed_at,
        )
        event = lease_due_corrections(
            now=observed_at + 1,
            settle_seconds=0,
            batch_size=1,
        )[0]
        apply_reflection_outcomes(
            [event],
            [
                ReflectionOutcome(
                    event.id,
                    event.event_revision,
                    "preference",
                    proposal,
                )
            ],
            now=observed_at + 2,
        )

    reflect("session-1", proposals[0], 1)
    reflect("session-2", proposals[1], 10)
    assert [item.preferred_value for item in search_preferences("TypeScript TSF")] == [
        "TypeScript"
    ]

    reflect("session-3", proposals[1], 20)
    assert [item.preferred_value for item in search_preferences("TypeScript TSF")] == [
        "TSF"
    ]
    with state_db.connection() as database:
        statuses = {
            row["preferred_value"]: row["status"]
            for row in database.execute(
                "SELECT preferred_value, status FROM learned_preferences"
            )
        }
    assert statuses == {"TypeScript": "superseded", "TSF": "active"}


def test_changed_event_revision_cannot_acknowledge_or_store_stale_result():
    record_correction(
        session_id="session-1",
        asr_text="原文",
        committed_text="原文",
        corrected_text="第一次修改",
        observed_at=10,
    )
    events = lease_due_corrections(now=20, settle_seconds=0)
    record_correction(
        session_id="session-1",
        asr_text="原文",
        committed_text="原文",
        corrected_text="第二次修改",
        observed_at=21,
    )
    outcome = ReflectionOutcome(
        events[0].id,
        events[0].event_revision,
        "preference",
        _proposal(),
    )

    assert apply_reflection_outcomes(events, [outcome], now=22) == (0, 1)
    with state_db.connection() as database:
        assert (
            database.execute("SELECT COUNT(*) FROM learned_preferences").fetchone()[0]
            == 0
        )
        row = database.execute(
            "SELECT event_revision, processed_revision FROM correction_events"
        ).fetchone()
    assert tuple(row) == (2, 0)


def test_failed_reflection_uses_retry_backoff():
    record_correction(
        session_id="session-1",
        asr_text="原文",
        committed_text="原文",
        corrected_text="修改",
        observed_at=10,
    )
    events = lease_due_corrections(now=20, settle_seconds=0)

    mark_correction_failure(
        events,
        "ValueError",
        now=20,
        base_delay_seconds=10,
        max_delay_seconds=60,
    )

    assert lease_due_corrections(now=29, settle_seconds=0) == []
    assert len(lease_due_corrections(now=30, settle_seconds=0)) == 1


def test_schema_upgrade_adds_asr_column_to_existing_history(
    tmp_path: Path, monkeypatch
):
    path = tmp_path / "legacy.db"
    database = sqlite3.connect(path)
    database.execute(
        "CREATE TABLE finalized_history("
        "sequence INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, "
        "original_text TEXT NOT NULL, current_text TEXT NOT NULL, "
        "tracking_status TEXT NOT NULL, committed_at REAL NOT NULL, "
        "modified_at REAL)"
    )
    database.commit()
    database.close()
    monkeypatch.setattr(state_db, "database_path", lambda: path)

    with state_db.connection() as upgraded:
        columns = {
            row["name"]
            for row in upgraded.execute("PRAGMA table_info(finalized_history)")
        }

    assert "asr_text" in columns


def test_empty_and_unrelated_tracked_text_do_not_enter_reflection_queue():
    assert not record_correction(
        session_id="empty",
        asr_text="原文",
        committed_text="已经上屏的完整语音文本",
        corrected_text="   ",
        observed_at=1,
    )
    assert record_correction(
        session_id="focus-moved",
        asr_text="原文",
        committed_text="这是一段已经上屏、稍后应该保持关联的完整语音输入。",
        corrected_text="修！",
        observed_at=2,
    )

    assert lease_due_corrections(now=10, settle_seconds=0) == []
    with state_db.connection() as database:
        rows = database.execute(
            "SELECT session_id, event_revision, processed_revision FROM correction_events"
        ).fetchall()
    assert [tuple(row) for row in rows] == [("focus-moved", 1, 1)]


def test_preference_crud_and_full_personalization_clear():
    record_correction(
        session_id="session-crud",
        asr_text="Type Script",
        committed_text="Type Script",
        corrected_text="TypeScript",
        observed_at=1,
    )
    event = lease_due_corrections(now=2, settle_seconds=0)[0]
    apply_reflection_outcomes(
        [event],
        [
            ReflectionOutcome(
                event.id,
                event.event_revision,
                "preference",
                _proposal(),
            )
        ],
        now=3,
    )
    items = cast(list[dict[str, object]], get_learned_preferences_snapshot()["items"])
    preference_id = int(cast(int, items[0]["id"]))

    update_learned_preference(
        preference_id=preference_id,
        kind="terminology",
        preferred_value="TypeScript SDK",
        avoid_values=("Type Script SDK",),
        keywords=("Type Script SDK",),
        status="candidate",
    )
    item = cast(list[dict[str, object]], get_learned_preferences_snapshot()["items"])[0]
    assert item["preferred_value"] == "TypeScript SDK"
    assert item["status"] == "candidate"
    assert item["keywords"] == ["Type Script SDK"]

    assert delete_learned_preference(preference_id)
    assert not delete_learned_preference(preference_id)
    assert get_learned_preferences_snapshot()["total"] == 0

    counts = clear_personalization_data()
    assert counts == {"corrections": 1, "preferences": 0, "reflection_runs": 0}
    corrections = cast(
        dict[str, int], get_reflection_store_snapshot(now=10)["corrections"]
    )
    assert corrections["total"] == 0
