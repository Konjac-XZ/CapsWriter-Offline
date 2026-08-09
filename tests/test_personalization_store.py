import sqlite3
from pathlib import Path

import pytest

from src.infra import state_db
from src.personalization.store import (
    PreferenceProposal,
    ReflectionOutcome,
    apply_reflection_outcomes,
    get_reflection_store_snapshot,
    lease_due_corrections,
    mark_correction_failure,
    record_correction,
    search_preferences,
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
        confidence=0.95,
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
    matches = search_preferences("我们用 TypeScript 开发前端", now=30)

    assert len(matches) == 1
    assert matches[0].preferred_value == "TypeScript"
    assert "TypeScript" in matches[0].matched_keywords
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
        confidence=0.9,
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
            0.9,
        ),
        PreferenceProposal(
            "terminology",
            "TSF",
            ("TypeScript",),
            ("TypeScript", "TSF"),
            0.95,
        ),
    )

    def reflect(session: str, proposal: PreferenceProposal, observed_at: float) -> None:
        record_correction(
            session_id=session,
            asr_text="术语",
            committed_text=proposal.avoid_values[0],
            corrected_text=proposal.preferred_value,
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
