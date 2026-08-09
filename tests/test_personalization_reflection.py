import asyncio
import contextlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.infra import state_db
from src.personalization import reflection
from src.personalization.reflection import (
    ReflectionSettings,
    _process_reflection_batch,
    parse_reflection_response,
)
from src.personalization.store import (
    CorrectionEvent,
    lease_due_corrections,
    record_correction,
)
from src.polish.providers.base import PolishCompletionResult


@pytest.fixture(autouse=True)
def isolate_state_database(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(state_db, "database_path", lambda: tmp_path / "state.db")


def _event() -> CorrectionEvent:
    return CorrectionEvent(
        id=1,
        session_id="session-1",
        asr_text="Type Script",
        committed_text="Type Script",
        corrected_text="TypeScript",
        event_revision=2,
        processed_revision=0,
        first_observed_at=1,
        last_observed_at=2,
        attempt_count=0,
    )


def _response(event_id: int, revision: int) -> str:
    return json.dumps(
        {
            "results": [
                {
                    "event_id": event_id,
                    "event_revision": revision,
                    "classification": "preference",
                    "preference": {
                        "kind": "spelling",
                        "preferred_value": "TypeScript",
                        "avoid_values": ["Type Script"],
                        "keywords": ["TypeScript", "Type Script"],
                        "confidence": 0.95,
                    },
                }
            ]
        },
        ensure_ascii=False,
    )


def test_parse_reflection_response_requires_exact_event_coverage():
    event = _event()

    outcomes = parse_reflection_response(
        f"```json\n{_response(event.id, event.event_revision)}\n```",
        [event],
    )

    assert len(outcomes) == 1
    assert outcomes[0].proposal is not None
    assert outcomes[0].proposal.preferred_value == "TypeScript"
    with pytest.raises(ValueError, match="unexpected event"):
        parse_reflection_response(_response(999, event.event_revision), [event])


def test_non_preference_result_must_not_smuggle_preference_payload():
    event = _event()
    payload = json.loads(_response(event.id, event.event_revision))
    payload["results"][0]["classification"] = "discard"

    with pytest.raises(ValueError, match="must use null"):
        parse_reflection_response(json.dumps(payload), [event])


def test_process_batch_persists_valid_provider_response(monkeypatch):
    record_correction(
        session_id="session-1",
        asr_text="Type Script",
        committed_text="Type Script",
        corrected_text="TypeScript",
        observed_at=1,
    )
    events = lease_due_corrections(now=2, settle_seconds=0)

    class FakeProvider:
        async def complete(self, request):
            return PolishCompletionResult(
                _response(events[0].id, events[0].event_revision),
                200,
            )

    async def fake_get_provider(config):
        return FakeProvider()

    context = SimpleNamespace(
        provider_name="openai_compatible",
        api_key="test-key",
        base_url="https://example.test",
        model="test-model",
        timeout_s=5.0,
        provider_options={"reuse_client": True},
    )
    monkeypatch.setattr(reflection, "_prepare_background_context", lambda: context)
    monkeypatch.setattr(reflection, "get_polish_provider", fake_get_provider)

    asyncio.run(
        _process_reflection_batch(
            events,
            ReflectionSettings(settle_seconds=0, retry_base_seconds=1),
        )
    )

    with state_db.connection() as database:
        correction = database.execute(
            "SELECT event_revision, processed_revision FROM correction_events"
        ).fetchone()
        preference = database.execute(
            "SELECT preferred_value, status FROM learned_preferences"
        ).fetchone()
        run = database.execute("SELECT outcome FROM reflection_runs").fetchone()
    assert tuple(correction) == (1, 1)
    assert tuple(preference) == ("TypeScript", "active")
    assert run["outcome"] == "success"


def test_process_batch_keeps_invalid_response_retryable(monkeypatch):
    record_correction(
        session_id="session-1",
        asr_text="原文",
        committed_text="原文",
        corrected_text="修改",
        observed_at=1,
    )
    events = lease_due_corrections(now=2, settle_seconds=0)

    class FakeProvider:
        async def complete(self, request):
            return PolishCompletionResult("not json", 200)

    async def fake_get_provider(config):
        return FakeProvider()

    context = SimpleNamespace(
        provider_name="openai_compatible",
        api_key="test-key",
        base_url="https://example.test",
        model="test-model",
        timeout_s=5.0,
        provider_options={"reuse_client": True},
    )
    monkeypatch.setattr(reflection, "_prepare_background_context", lambda: context)
    monkeypatch.setattr(reflection, "get_polish_provider", fake_get_provider)

    asyncio.run(
        _process_reflection_batch(
            events,
            ReflectionSettings(
                settle_seconds=0,
                retry_base_seconds=1,
                retry_max_seconds=10,
            ),
        )
    )

    with state_db.connection() as database:
        correction = database.execute(
            "SELECT processed_revision, attempt_count, next_attempt_at, lease_until "
            "FROM correction_events"
        ).fetchone()
        run = database.execute(
            "SELECT outcome, error_type FROM reflection_runs"
        ).fetchone()
    assert correction["processed_revision"] == 0
    assert correction["attempt_count"] == 1
    assert correction["next_attempt_at"] > 0
    assert correction["lease_until"] == 0
    assert tuple(run) == ("failure", "JSONDecodeError")


def test_foreground_cancellation_of_request_does_not_stop_worker(monkeypatch):
    event = _event()
    monkeypatch.setattr(
        reflection,
        "get_reflection_settings",
        lambda: ReflectionSettings(enabled=True, poll_seconds=60),
    )
    monkeypatch.setattr(reflection, "_is_foreground_idle", lambda: True)
    monkeypatch.setattr(reflection, "lease_due_corrections", lambda **_kwargs: [event])

    async def cancelled_batch(events, settings):
        raise asyncio.CancelledError

    monkeypatch.setattr(reflection, "_process_reflection_batch", cancelled_batch)

    async def exercise() -> bool:
        worker = asyncio.create_task(reflection.run_reflection_worker())
        await asyncio.sleep(0.05)
        still_running = not worker.done()
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
        return still_running

    assert asyncio.run(exercise()) is True
