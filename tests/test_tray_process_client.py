from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from PySide6.QtCore import QCoreApplication, QFileSystemWatcher

from start_client_gui import GUI
from src.gui import tray_process_client


class _ProcessStub:
    def poll(self) -> None:
        return None


def _wait_for_directory_event(
    app: QCoreApplication, events: list[str], previous_count: int
) -> None:
    deadline = time.monotonic() + 2.0
    while len(events) == previous_count and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert len(events) > previous_count


def test_directory_watcher_survives_repeated_atomic_replaces(tmp_path: Path) -> None:
    app = QCoreApplication.instance() or QCoreApplication([])
    watcher = QFileSystemWatcher([str(tmp_path)])
    events: list[str] = []
    watcher.directoryChanged.connect(events.append)
    target = tmp_path / "event.json"

    for serial in (1, 2):
        previous_count = len(events)
        temporary = tmp_path / ".event.json.tmp"
        temporary.write_text(json.dumps({"serial": serial}), encoding="utf-8")
        temporary.replace(target)
        _wait_for_directory_event(app, events, previous_count)

    assert watcher.directories() == [str(tmp_path)]


def test_start_launches_independent_tray_module(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        tray_process_client.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(tmp_path),
    )
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(command: list[str], **kwargs: object) -> _ProcessStub:
        popen_calls.append((command, kwargs))
        return _ProcessStub()

    monkeypatch.setattr(tray_process_client.subprocess, "Popen", fake_popen)
    client = tray_process_client.TrayProcessClient(tmp_path, "pythonw.exe")

    assert client.session_dir == tmp_path

    assert client.start() is True
    assert popen_calls[0][0][:4] == [
        "pythonw.exe",
        "-m",
        "src.gui.tray_process",
        str(tmp_path),
    ]


def test_poll_event_returns_each_tray_action_once(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        tray_process_client.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(tmp_path),
    )
    client = tray_process_client.TrayProcessClient(tmp_path, "pythonw.exe")
    event_path = tmp_path / "event.json"
    event_path.write_text('{"serial": 1, "event": "show"}', encoding="utf-8")

    assert client.poll_event() == {"serial": 1, "event": "show"}
    assert client.poll_event() is None

    event_path.write_text(
        json.dumps({"serial": 2, "event": "reload_providers"}),
        encoding="utf-8",
    )
    assert client.poll_event() == {"serial": 2, "event": "reload_providers"}


@pytest.mark.parametrize(
    ("event_name", "handler_name"),
    [
        ("show", "_show_from_tray"),
        ("reload_providers", "reload_providers"),
        ("restart_client", "restart_client"),
        ("quit", "quit_app"),
    ],
)
def test_main_gui_dispatches_tray_actions(event_name: str, handler_name: str) -> None:
    calls: list[str] = []
    process_client = SimpleNamespace(
        is_running=lambda: True,
        poll_event=lambda: {"serial": 1, "event": event_name},
    )
    owner = cast(GUI, SimpleNamespace(_tray_process_client=process_client))
    for name in ("_show_from_tray", "reload_providers", "restart_client", "quit_app"):
        setattr(owner, name, lambda selected=name: calls.append(selected))

    GUI._poll_tray_process_event(owner)

    assert calls == [handler_name]
