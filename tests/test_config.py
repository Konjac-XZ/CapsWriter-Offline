from pathlib import Path

import pytest
from pydantic import ValidationError

from src.infra.config import _parse_client_config, config


def test_client_config_ignores_removed_legacy_keys() -> None:
    parsed = _parse_client_config(
        {
            "mic_seg_duration": 15,
            "mic_seg_overlap": 2,
            "file_seg_duration": 25,
            "file_seg_overlap": 2,
            "vscode_exe_path": r"C:\Program Files\Microsoft VS Code\Code.exe",
        }
    )

    assert not hasattr(parsed, "mic_seg_duration")
    assert not hasattr(parsed, "mic_seg_overlap")
    assert not hasattr(parsed, "file_seg_duration")
    assert not hasattr(parsed, "file_seg_overlap")
    assert not hasattr(parsed, "vscode_exe_path")


def test_client_config_converts_paths_and_accepts_minimums() -> None:
    parsed = _parse_client_config(
        {
            "start_music_path": "assets/start.mp3",
            "stop_music_path": "assets/stop.mp3",
            "tsf_speech_tip_ack_timeout_ms": 10,
            "daily_input_log_interval": 1,
        }
    )

    assert parsed.start_music_path == Path("assets/start.mp3")
    assert parsed.stop_music_path == Path("assets/stop.mp3")
    assert parsed.tsf_speech_tip_ack_timeout_ms == 10
    assert parsed.daily_input_log_interval == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"paste": "false"},
        {"tsf_speech_tip_ack_timeout_ms": 9},
        {"daily_input_log_interval": 0},
    ],
)
def test_client_config_rejects_invalid_values(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        _parse_client_config(payload)


def test_loaded_config_has_no_removed_attributes() -> None:
    assert not hasattr(config, "mic_seg_duration")
    assert not hasattr(config, "file_seg_duration")
    assert not hasattr(config, "vscode_exe_path")
