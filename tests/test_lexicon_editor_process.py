from __future__ import annotations

import json
import os
from pathlib import Path

from src.gui import lexicon_editor_process


class _DialogStub:
    def __init__(self) -> None:
        self.visible = False
        self.prepared_texts: list[str] = []
        self.open_count = 0

    def isVisible(self) -> bool:
        return self.visible

    def prepare_for_open(self, text: str) -> None:
        self.prepared_texts.append(text)

    def open(self) -> None:
        self.open_count += 1
        self.visible = True

    def raise_(self) -> None:
        pass

    def activateWindow(self) -> None:
        pass


class _TimerStub:
    def __init__(self) -> None:
        self.started_with: list[int] = []
        self.stop_count = 0

    def start(self, interval: int) -> None:
        self.started_with.append(interval)

    def stop(self) -> None:
        self.stop_count += 1


def test_windows_parent_probe_never_calls_os_kill(monkeypatch) -> None:
    kill_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(lexicon_editor_process.os, "name", "nt")
    monkeypatch.setattr(
        lexicon_editor_process.os,
        "kill",
        lambda pid, signal: kill_calls.append((pid, signal)),
    )
    monkeypatch.setattr(lexicon_editor_process, "_windows_process_exists", lambda pid: pid == 42)

    assert lexicon_editor_process._process_exists(42) is True
    assert lexicon_editor_process._process_exists(41) is False
    assert kill_calls == []


def test_windows_process_probe_reports_current_process_alive() -> None:
    if os.name != "nt":
        return
    assert lexicon_editor_process._windows_process_exists(os.getpid()) is True


def test_each_show_command_refreshes_latest_disk_text(monkeypatch, tmp_path: Path) -> None:
    disk_texts = iter(("words:\n- first\n", "words:\n- second\n"))
    monkeypatch.setattr(lexicon_editor_process, "read_lexicon_text", lambda: next(disk_texts))
    dialog = _DialogStub()
    service = lexicon_editor_process.LexiconEditorService.__new__(
        lexicon_editor_process.LexiconEditorService
    )
    service._command_path = tmp_path / "command.json"
    service._event_path = tmp_path / "event.json"
    service._last_command_serial = 0
    service._active_serial = None
    service._dialog = dialog
    service._dialog_ready = True
    service._idle_timer = _TimerStub()

    service._command_path.write_text('{"serial": 1, "command": "show"}', encoding="utf-8")
    service._poll_command()
    dialog.visible = False
    service._command_path.write_text('{"serial": 2, "command": "show"}', encoding="utf-8")
    service._poll_command()

    assert dialog.prepared_texts == ["words:\n- first\n", "words:\n- second\n"]
    assert dialog.open_count == 2
    assert service._active_serial == 2
    assert json.loads(service._event_path.read_text(encoding="utf-8"))["event"] == "opened"


def test_first_show_schedules_lazy_editor_initialization(monkeypatch, tmp_path: Path) -> None:
    scheduled_callbacks: list[object] = []
    monkeypatch.setattr(
        lexicon_editor_process.QTimer,
        "singleShot",
        lambda delay, callback: scheduled_callbacks.append((delay, callback)),
    )
    service = lexicon_editor_process.LexiconEditorService.__new__(
        lexicon_editor_process.LexiconEditorService
    )
    service._command_path = tmp_path / "command.json"
    service._event_path = tmp_path / "event.json"
    service._last_command_serial = 0
    service._active_serial = None
    service._pending_show_serial = None
    service._dialog = None
    service._dialog_ready = False
    service._idle_timer = _TimerStub()
    service._command_path.write_text('{"serial": 1, "command": "show"}', encoding="utf-8")

    service._poll_command()

    assert service._dialog is None
    assert service._pending_show_serial == 1
    assert scheduled_callbacks == [(0, service._initialize_dialog)]


def test_visible_editor_is_not_overwritten_by_second_show(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        lexicon_editor_process,
        "read_lexicon_text",
        lambda: "words:\n- should-not-load\n",
    )
    dialog = _DialogStub()
    dialog.visible = True
    service = lexicon_editor_process.LexiconEditorService.__new__(
        lexicon_editor_process.LexiconEditorService
    )
    service._command_path = tmp_path / "command.json"
    service._event_path = tmp_path / "event.json"
    service._last_command_serial = 0
    service._active_serial = 1
    service._dialog = dialog
    service._idle_timer = _TimerStub()
    service._command_path.write_text('{"serial": 2, "command": "show"}', encoding="utf-8")

    service._poll_command()

    assert dialog.prepared_texts == []
    assert json.loads(service._event_path.read_text(encoding="utf-8"))["event"] == "already_open"


def test_dialog_finish_starts_five_minute_idle_timer(tmp_path: Path) -> None:
    timer = _TimerStub()
    service = lexicon_editor_process.LexiconEditorService.__new__(
        lexicon_editor_process.LexiconEditorService
    )
    service._event_path = tmp_path / "event.json"
    service._active_serial = 7
    service._idle_timer = timer

    service._dialog_finished(0)

    assert timer.started_with == [5 * 60 * 1000]
    assert json.loads(service._event_path.read_text(encoding="utf-8")) == {
        "serial": 7,
        "event": "cancelled",
    }


def test_idle_timer_quits_only_when_editor_is_not_in_use(monkeypatch) -> None:
    quit_calls: list[bool] = []
    monkeypatch.setattr(
        lexicon_editor_process.QApplication,
        "quit",
        lambda: quit_calls.append(True),
    )
    dialog = _DialogStub()
    service = lexicon_editor_process.LexiconEditorService.__new__(
        lexicon_editor_process.LexiconEditorService
    )
    service._dialog = dialog
    service._pending_show_serial = None

    dialog.visible = True
    service._quit_if_idle()
    assert quit_calls == []

    dialog.visible = False
    service._quit_if_idle()
    assert quit_calls == [True]
