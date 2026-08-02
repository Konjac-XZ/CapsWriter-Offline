import pytest

from src.pipeline.recv_result import _transcription_delay


def test_transcription_delay_excludes_recording_time_for_realtime_session():
    message = {
        "time_submit": 100.00,
        "time_stop": 110.70,
        "time_complete": 110.96,
    }

    assert _transcription_delay(message) == pytest.approx(0.26)


def test_transcription_delay_uses_submit_time_for_legacy_message():
    message = {
        "time_submit": 110.70,
        "time_complete": 110.96,
    }

    assert _transcription_delay(message) == pytest.approx(0.26)
