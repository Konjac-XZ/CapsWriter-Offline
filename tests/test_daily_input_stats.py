import json
from datetime import date, timedelta
from pathlib import Path

from src.infra import daily_input_stats


class _Logger:
    def __init__(self) -> None:
        self.messages: list[tuple[str, tuple[object, ...]]] = []

    def info(self, message: str, *args: object) -> None:
        self.messages.append((message, args))


def test_record_input_characters_persists_today_and_logs_each_interval(
    monkeypatch, tmp_path: Path
) -> None:
    state_path = tmp_path / "State" / "daily_input.json"
    monkeypatch.setattr(daily_input_stats, "state_file_path", lambda: state_path)
    logger = _Logger()
    today = date(2026, 7, 15)

    assert (
        daily_input_stats.record_input_characters(
            "你好ab", log_interval=3, today=today, logger=logger
        )
        == 4
    )
    assert (
        daily_input_stats.record_input_characters(
            "cde", log_interval=3, today=today, logger=logger
        )
        == 7
    )
    assert daily_input_stats.get_today_input_count(today=today) == 7
    assert [args for _, args in logger.messages] == [(3,), (6,)]
    assert json.loads(state_path.read_text(encoding="utf-8"))["character_count"] == 7


def test_daily_count_resets_when_the_date_changes(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "State" / "daily_input.json"
    monkeypatch.setattr(daily_input_stats, "state_file_path", lambda: state_path)
    yesterday = date(2026, 7, 14)
    today = yesterday + timedelta(days=1)

    daily_input_stats.record_input_characters("abc", today=yesterday)

    assert daily_input_stats.get_today_input_count(today=today) == 0
    assert daily_input_stats.record_input_characters("中", today=today) == 1


def test_corrupt_state_is_treated_as_empty(monkeypatch, tmp_path: Path) -> None:
    state_path = tmp_path / "daily_input.json"
    state_path.write_text("not json", encoding="utf-8")
    monkeypatch.setattr(daily_input_stats, "state_file_path", lambda: state_path)

    assert daily_input_stats.record_input_characters("ab", today=date(2026, 7, 15)) == 2
