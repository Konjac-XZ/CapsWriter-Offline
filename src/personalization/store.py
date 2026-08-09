"""SQLite-backed correction journal and learned-preference store."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Iterable, Sequence

from src.infra import state_db


ALLOWED_PREFERENCE_KINDS = {
    "terminology",
    "spelling",
    "casing",
    "punctuation",
    "formatting",
    "style",
    "avoidance",
}
STRONG_SINGLE_EVIDENCE_KINDS = ALLOWED_PREFERENCE_KINDS - {"style"}
ACTIVE_CONFIDENCE_THRESHOLD = 0.75


@dataclass(frozen=True, slots=True)
class CorrectionEvent:
    id: int
    session_id: str
    asr_text: str
    committed_text: str
    corrected_text: str
    event_revision: int
    processed_revision: int
    first_observed_at: float
    last_observed_at: float
    attempt_count: int


@dataclass(frozen=True, slots=True)
class PreferenceProposal:
    kind: str
    preferred_value: str
    avoid_values: tuple[str, ...]
    keywords: tuple[str, ...]
    confidence: float


@dataclass(frozen=True, slots=True)
class ReflectionOutcome:
    event_id: int
    event_revision: int
    classification: str
    proposal: PreferenceProposal | None


@dataclass(frozen=True, slots=True)
class RetrievedPreference:
    id: int
    kind: str
    preferred_value: str
    avoid_values: tuple[str, ...]
    matched_keywords: tuple[str, ...]
    confidence: float
    evidence_count: int
    score: float


@dataclass(slots=True)
class _PreferenceAccumulator:
    kind: str
    preferred_value: str
    avoid_values_json: str
    confidence: float
    evidence_count: int
    matches: list[str] = field(default_factory=list)
    score: float = 0.0


def normalize_match_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"\s+", " ", normalized).strip()


def _canonical_key(proposal: PreferenceProposal) -> str:
    payload = json.dumps(
        {
            "kind": proposal.kind,
            "preferred": normalize_match_text(proposal.preferred_value),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def record_correction(
    *,
    session_id: str,
    asr_text: str,
    committed_text: str,
    corrected_text: str,
    observed_at: float | None = None,
) -> bool:
    """Upsert the latest trusted correction for a committed TSF session."""
    if not session_id or not committed_text.strip():
        return False
    now = time.time() if observed_at is None else float(observed_at)
    try:
        with state_db.connection() as database:
            database.execute("BEGIN IMMEDIATE")
            existing = database.execute(
                "SELECT id, corrected_text, event_revision FROM correction_events "
                "WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if existing is None:
                database.execute(
                    "INSERT INTO correction_events("
                    "session_id, asr_text, committed_text, corrected_text, "
                    "event_revision, processed_revision, first_observed_at, "
                    "last_observed_at, attempt_count, next_attempt_at, lease_until"
                    ") VALUES(?, ?, ?, ?, 1, ?, ?, ?, 0, 0, 0)",
                    (
                        session_id,
                        asr_text.strip(),
                        committed_text.strip(),
                        corrected_text.strip(),
                        1 if corrected_text.strip() == committed_text.strip() else 0,
                        now,
                        now,
                    ),
                )
            elif str(existing["corrected_text"]) != corrected_text.strip():
                revision = int(existing["event_revision"]) + 1
                database.execute(
                    "UPDATE correction_events SET asr_text = ?, committed_text = ?, "
                    "corrected_text = ?, event_revision = ?, last_observed_at = ?, "
                    "attempt_count = 0, next_attempt_at = 0, lease_until = 0, "
                    "last_error = NULL, processed_revision = CASE WHEN ? = ? "
                    "THEN ? ELSE processed_revision END WHERE id = ?",
                    (
                        asr_text.strip(),
                        committed_text.strip(),
                        corrected_text.strip(),
                        revision,
                        now,
                        corrected_text.strip(),
                        committed_text.strip(),
                        revision,
                        int(existing["id"]),
                    ),
                )
            database.commit()
        return True
    except (OSError, sqlite3.Error, TypeError, ValueError):
        return False


def lease_due_corrections(
    *,
    now: float | None = None,
    settle_seconds: float = 120.0,
    batch_size: int = 6,
    lease_seconds: float = 180.0,
) -> list[CorrectionEvent]:
    current_time = time.time() if now is None else float(now)
    cutoff = current_time - max(0.0, float(settle_seconds))
    limit = max(1, min(50, int(batch_size)))
    with state_db.connection() as database:
        database.execute("BEGIN IMMEDIATE")
        rows = database.execute(
            "SELECT id, session_id, asr_text, committed_text, corrected_text, "
            "event_revision, processed_revision, first_observed_at, "
            "last_observed_at, attempt_count FROM correction_events "
            "WHERE event_revision > processed_revision "
            "AND corrected_text <> committed_text "
            "AND last_observed_at <= ? AND next_attempt_at <= ? "
            "AND lease_until <= ? ORDER BY last_observed_at, id LIMIT ?",
            (cutoff, current_time, current_time, limit),
        ).fetchall()
        if rows:
            lease_until = current_time + max(10.0, float(lease_seconds))
            database.executemany(
                "UPDATE correction_events SET lease_until = ? "
                "WHERE id = ? AND event_revision = ?",
                [
                    (lease_until, int(row["id"]), int(row["event_revision"]))
                    for row in rows
                ],
            )
        database.commit()
    return [_event_from_row(row) for row in rows]


def _event_from_row(row: sqlite3.Row) -> CorrectionEvent:
    return CorrectionEvent(
        id=int(row["id"]),
        session_id=str(row["session_id"]),
        asr_text=str(row["asr_text"]),
        committed_text=str(row["committed_text"]),
        corrected_text=str(row["corrected_text"]),
        event_revision=int(row["event_revision"]),
        processed_revision=int(row["processed_revision"]),
        first_observed_at=float(row["first_observed_at"]),
        last_observed_at=float(row["last_observed_at"]),
        attempt_count=int(row["attempt_count"]),
    )


def release_correction_leases(events: Sequence[CorrectionEvent]) -> None:
    if not events:
        return
    with state_db.connection() as database:
        database.executemany(
            "UPDATE correction_events SET lease_until = 0 "
            "WHERE id = ? AND event_revision = ?",
            [(event.id, event.event_revision) for event in events],
        )
        database.commit()


def mark_correction_failure(
    events: Sequence[CorrectionEvent],
    error_type: str,
    *,
    now: float | None = None,
    base_delay_seconds: float = 60.0,
    max_delay_seconds: float = 3600.0,
) -> None:
    if not events:
        return
    current_time = time.time() if now is None else float(now)
    rows: list[tuple[int, float, str, int, int]] = []
    for event in events:
        attempts = event.attempt_count + 1
        delay = min(
            max(1.0, float(max_delay_seconds)),
            max(1.0, float(base_delay_seconds)) * (2 ** min(attempts - 1, 8)),
        )
        rows.append(
            (
                attempts,
                current_time + delay,
                (error_type or "unknown")[:200],
                event.id,
                event.event_revision,
            )
        )
    with state_db.connection() as database:
        database.executemany(
            "UPDATE correction_events SET attempt_count = ?, next_attempt_at = ?, "
            "lease_until = 0, last_error = ? WHERE id = ? AND event_revision = ?",
            rows,
        )
        database.commit()


def apply_reflection_outcomes(
    events: Sequence[CorrectionEvent],
    outcomes: Sequence[ReflectionOutcome],
    *,
    now: float | None = None,
) -> tuple[int, int]:
    """Atomically store valid outcomes and acknowledge unchanged event revisions."""
    current_time = time.time() if now is None else float(now)
    event_by_key = {(event.id, event.event_revision): event for event in events}
    outcome_by_key = {
        (outcome.event_id, outcome.event_revision): outcome for outcome in outcomes
    }
    if set(outcome_by_key) != set(event_by_key):
        raise ValueError("reflection response does not cover the leased event batch")

    applied = 0
    stale = 0
    with state_db.connection() as database:
        database.execute("BEGIN IMMEDIATE")
        for key, event in event_by_key.items():
            row = database.execute(
                "SELECT event_revision FROM correction_events WHERE id = ?",
                (event.id,),
            ).fetchone()
            if row is None or int(row["event_revision"]) != event.event_revision:
                stale += 1
                continue

            outcome = outcome_by_key[key]
            if outcome.proposal is not None:
                _upsert_preference(database, event, outcome.proposal, current_time)
            database.execute(
                "UPDATE correction_events SET processed_revision = ?, "
                "attempt_count = 0, next_attempt_at = 0, lease_until = 0, "
                "last_error = NULL WHERE id = ? AND event_revision = ?",
                (event.event_revision, event.id, event.event_revision),
            )
            applied += 1
        database.commit()
    return applied, stale


def _upsert_preference(
    database: sqlite3.Connection,
    event: CorrectionEvent,
    proposal: PreferenceProposal,
    now: float,
) -> int:
    if proposal.kind not in ALLOWED_PREFERENCE_KINDS:
        raise ValueError(f"unsupported preference kind: {proposal.kind}")
    canonical_key = _canonical_key(proposal)
    stored_avoid_values = _dedupe_values(proposal.avoid_values)
    existing = database.execute(
        "SELECT id, evidence_count, confidence, avoid_values_json "
        "FROM learned_preferences "
        "WHERE canonical_key = ?",
        (canonical_key,),
    ).fetchone()
    if existing is None:
        cursor = database.execute(
            "INSERT INTO learned_preferences("
            "canonical_key, kind, preferred_value, avoid_values_json, confidence, "
            "status, evidence_count, created_at, updated_at) "
            "VALUES(?, ?, ?, ?, ?, 'candidate', 0, ?, ?)",
            (
                canonical_key,
                proposal.kind,
                proposal.preferred_value,
                json.dumps(stored_avoid_values, ensure_ascii=False),
                proposal.confidence,
                now,
                now,
            ),
        )
        if cursor.lastrowid is None:
            raise sqlite3.DatabaseError("preference insert did not return an id")
        preference_id = int(cursor.lastrowid)
    else:
        preference_id = int(existing["id"])
        old_count = int(existing["evidence_count"])
        old_confidence = float(existing["confidence"])
        try:
            old_avoid_values = json.loads(str(existing["avoid_values_json"]))
        except json.JSONDecodeError:
            old_avoid_values = []
        merged_avoid_values = _dedupe_values(
            (
                *(str(value) for value in old_avoid_values if isinstance(value, str)),
                *proposal.avoid_values,
            )
        )
        stored_avoid_values = merged_avoid_values
        confidence = ((old_confidence * old_count) + proposal.confidence) / max(
            1, old_count + 1
        )
        database.execute(
            "UPDATE learned_preferences SET preferred_value = ?, "
            "avoid_values_json = ?, confidence = ?, updated_at = ? WHERE id = ?",
            (
                proposal.preferred_value,
                json.dumps(merged_avoid_values, ensure_ascii=False),
                confidence,
                now,
                preference_id,
            ),
        )

    database.execute(
        "INSERT OR IGNORE INTO preference_evidence("
        "preference_id, correction_event_id, event_revision, committed_text, "
        "corrected_text, observed_at) VALUES(?, ?, ?, ?, ?, ?)",
        (
            preference_id,
            event.id,
            event.event_revision,
            event.committed_text,
            event.corrected_text,
            event.last_observed_at,
        ),
    )
    evidence_count = int(
        database.execute(
            "SELECT COUNT(*) AS count FROM preference_evidence WHERE preference_id = ?",
            (preference_id,),
        ).fetchone()["count"]
    )
    row = database.execute(
        "SELECT confidence FROM learned_preferences WHERE id = ?",
        (preference_id,),
    ).fetchone()
    confidence = float(row["confidence"])
    active = confidence >= ACTIVE_CONFIDENCE_THRESHOLD and (
        proposal.kind in STRONG_SINGLE_EVIDENCE_KINDS or evidence_count >= 2
    )
    status = "active" if active else "candidate"
    if active:
        conflicts = _find_conflicting_active_preferences(
            database,
            preference_id=preference_id,
            kind=proposal.kind,
            preferred_value=proposal.preferred_value,
            avoid_values=stored_avoid_values,
        )
        if conflicts:
            can_supersede = evidence_count >= 2 and all(
                confidence >= conflict_confidence
                for _, conflict_confidence in conflicts
            )
            if can_supersede:
                database.executemany(
                    "UPDATE learned_preferences SET status = 'superseded', "
                    "updated_at = ? WHERE id = ?",
                    [(now, conflict_id) for conflict_id, _ in conflicts],
                )
            else:
                status = "candidate"
    database.execute(
        "UPDATE learned_preferences SET evidence_count = ?, status = ?, "
        "updated_at = ? WHERE id = ?",
        (evidence_count, status, now, preference_id),
    )

    keyword_values = _dedupe_values(
        (*proposal.keywords, proposal.preferred_value, *proposal.avoid_values)
    )
    database.executemany(
        "INSERT INTO preference_keywords("
        "preference_id, keyword, normalized_keyword, weight) VALUES(?, ?, ?, 1) "
        "ON CONFLICT(preference_id, normalized_keyword) DO UPDATE SET "
        "keyword = excluded.keyword, weight = MAX(weight, excluded.weight)",
        [
            (preference_id, keyword, normalize_match_text(keyword))
            for keyword in keyword_values
            if _valid_keyword(keyword)
        ],
    )
    return preference_id


def _find_conflicting_active_preferences(
    database: sqlite3.Connection,
    *,
    preference_id: int,
    kind: str,
    preferred_value: str,
    avoid_values: Sequence[str],
) -> list[tuple[int, float]]:
    preferred_normalized = normalize_match_text(preferred_value)
    avoid_normalized = {normalize_match_text(value) for value in avoid_values}
    rows = database.execute(
        "SELECT id, preferred_value, avoid_values_json, confidence "
        "FROM learned_preferences WHERE status = 'active' AND kind = ? AND id <> ?",
        (kind, preference_id),
    ).fetchall()
    conflicts: list[tuple[int, float]] = []
    for row in rows:
        existing_preferred = normalize_match_text(str(row["preferred_value"]))
        try:
            existing_avoid_raw = json.loads(str(row["avoid_values_json"]))
        except json.JSONDecodeError:
            existing_avoid_raw = []
        existing_avoid = {
            normalize_match_text(str(value))
            for value in existing_avoid_raw
            if isinstance(value, str)
        }
        if (
            existing_preferred in avoid_normalized
            or preferred_normalized in existing_avoid
        ):
            conflicts.append((int(row["id"]), float(row["confidence"])))
    return conflicts


def _dedupe_values(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        cleaned = value.strip()
        normalized = normalize_match_text(cleaned)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result.append(cleaned)
    return tuple(result)


def _valid_keyword(value: str) -> bool:
    normalized = normalize_match_text(value)
    if not normalized or len(normalized) > 80:
        return False
    if all(
        char.isspace() or unicodedata.category(char).startswith("P")
        for char in normalized
    ):
        return False
    if len(normalized) >= 2:
        return True
    return normalized in {"r", "c"}


def search_preferences(
    asr_text: str,
    textbox_context: str | None = None,
    *,
    max_results: int = 5,
    now: float | None = None,
) -> list[RetrievedPreference]:
    normalized_asr = normalize_match_text(asr_text)
    normalized_context = normalize_match_text(textbox_context or "")
    if not normalized_asr and not normalized_context:
        return []
    with state_db.connection() as database:
        rows = database.execute(
            "SELECT p.id, p.kind, p.preferred_value, p.avoid_values_json, "
            "p.confidence, p.evidence_count, k.keyword, k.normalized_keyword, "
            "k.weight FROM learned_preferences p "
            "JOIN preference_keywords k ON k.preference_id = p.id "
            "WHERE p.status = 'active' AND ("
            "instr(?, k.normalized_keyword) > 0 OR "
            "instr(?, k.normalized_keyword) > 0) "
            "ORDER BY p.id, k.normalized_keyword",
            (normalized_asr, normalized_context),
        ).fetchall()

    grouped: dict[int, _PreferenceAccumulator] = {}
    for row in rows:
        preference_id = int(row["id"])
        entry = grouped.setdefault(
            preference_id,
            _PreferenceAccumulator(
                kind=str(row["kind"]),
                preferred_value=str(row["preferred_value"]),
                avoid_values_json=str(row["avoid_values_json"]),
                confidence=float(row["confidence"]),
                evidence_count=int(row["evidence_count"]),
            ),
        )
        keyword = str(row["keyword"])
        normalized_keyword = str(row["normalized_keyword"])
        asr_hit = _keyword_matches(normalized_keyword, normalized_asr)
        context_hit = _keyword_matches(normalized_keyword, normalized_context)
        if not asr_hit and not context_hit:
            continue
        entry.matches.append(keyword)
        specificity = min(2.0, len(normalized_keyword) / 8.0)
        entry.score += (
            (3.0 if asr_hit else 0.0)
            + (1.25 if context_hit else 0.0)
            + float(row["weight"])
            + specificity
        )

    results: list[RetrievedPreference] = []
    for preference_id, entry in grouped.items():
        if not entry.matches:
            continue
        try:
            avoid_values_raw = json.loads(entry.avoid_values_json)
        except json.JSONDecodeError:
            avoid_values_raw = []
        avoid_values = tuple(
            str(value) for value in avoid_values_raw if isinstance(value, str)
        )
        confidence = entry.confidence
        evidence_count = entry.evidence_count
        score = entry.score + confidence + (0.25 * math.log1p(evidence_count))
        results.append(
            RetrievedPreference(
                id=preference_id,
                kind=entry.kind,
                preferred_value=entry.preferred_value,
                avoid_values=avoid_values,
                matched_keywords=_dedupe_values(entry.matches),
                confidence=confidence,
                evidence_count=evidence_count,
                score=score,
            )
        )
    results.sort(key=lambda item: (-item.score, item.id))
    selected = results[: max(1, min(20, int(max_results)))]
    if selected:
        matched_at = time.time() if now is None else float(now)
        with state_db.connection() as database:
            database.executemany(
                "UPDATE learned_preferences SET last_matched_at = ?, "
                "match_count = match_count + 1 WHERE id = ?",
                [(matched_at, item.id) for item in selected],
            )
            database.commit()
    return selected


def _keyword_matches(keyword: str, text: str) -> bool:
    if not keyword or not text:
        return False
    if re.fullmatch(r"[a-z0-9_+#.\-]+", keyword):
        return (
            re.search(
                rf"(?<![a-z0-9_]){re.escape(keyword)}(?![a-z0-9_])",
                text,
            )
            is not None
        )
    return keyword in text


def record_reflection_run(
    *,
    started_at: float,
    provider: str,
    model: str,
    event_count: int,
    outcome: str,
    completed_at: float | None = None,
    response_hash: str | None = None,
    error_type: str | None = None,
) -> None:
    with state_db.connection() as database:
        database.execute(
            "INSERT INTO reflection_runs("
            "started_at, completed_at, provider, model, event_count, outcome, "
            "response_hash, error_type) VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
            (
                started_at,
                completed_at,
                provider,
                model,
                event_count,
                outcome,
                response_hash,
                error_type,
            ),
        )
        database.commit()
