from __future__ import annotations

import json
import os
from pathlib import Path

from src.gui import tray_process


def test_windows_parent_probe_never_calls_os_kill(monkeypatch) -> None:
    kill_calls: list[tuple[int, int]] = []
    monkeypatch.setattr(tray_process.os, "name", "nt")
    monkeypatch.setattr(
        tray_process.os,
        "kill",
        lambda pid, signal: kill_calls.append((pid, signal)),
    )
    monkeypatch.setattr(tray_process, "_windows_process_exists", lambda pid: pid == 42)

    assert tray_process._process_exists(42) is True
    assert tray_process._process_exists(41) is False
    assert kill_calls == []


def test_windows_process_probe_reports_current_process_alive() -> None:
    if os.name != "nt":
        return
    assert tray_process._windows_process_exists(os.getpid()) is True


def test_emit_event_increments_serial_and_writes_action(tmp_path: Path) -> None:
    service = tray_process.TrayService.__new__(tray_process.TrayService)
    service._event_path = tmp_path / "event.json"
    service._event_serial = 0

    service._emit_event("show")
    first = json.loads(service._event_path.read_text(encoding="utf-8"))
    service._emit_event("reload_providers")
    second = json.loads(service._event_path.read_text(encoding="utf-8"))

    assert first == {"serial": 1, "event": "show"}
    assert second == {"serial": 2, "event": "reload_providers"}
