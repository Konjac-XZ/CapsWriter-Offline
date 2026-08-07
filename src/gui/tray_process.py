from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QFileSystemWatcher, QObject, QTimer
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from src.gui.runtime import client_icon_path
from src.gui.tray_menu import AutoDismissTrayMenu


PARENT_POLL_INTERVAL_MS = 2000


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary_path.replace(path)


def _windows_process_exists(pid: int) -> bool:
    process_synchronize = 0x00100000
    wait_timeout = 0x00000102
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenProcess.restype = ctypes.c_void_p
    kernel32.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
    kernel32.WaitForSingleObject.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    handle = kernel32.OpenProcess(process_synchronize, 0, pid)
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(handle, 0) == wait_timeout
    finally:
        kernel32.CloseHandle(handle)


def _process_exists(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        return _windows_process_exists(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


class TrayService(QObject):
    """Own the tray icon and menu outside the main CapsWriter GUI process."""

    def __init__(self, session_dir: Path, parent_pid: int) -> None:
        super().__init__()
        self._command_path = session_dir / "command.json"
        self._event_path = session_dir / "event.json"
        self._parent_pid = parent_pid
        self._last_command_serial = 0
        self._event_serial = 0

        self._tray_icon = QSystemTrayIcon(self)
        self._tray_icon.setIcon(QIcon(str(client_icon_path())))
        self._tray_icon.activated.connect(self._tray_activated)

        self._menu = AutoDismissTrayMenu()
        self._add_action("⚡ Reload Providers", "reload_providers")
        self._menu.addSeparator()
        self._add_action("🪟 Show", "show")
        self._add_action("🔄 Restart Client", "restart_client")
        self._add_action("❌ Quit", "quit")
        self._tray_icon.setContextMenu(self._menu)
        self._tray_icon.show()

        self._command_watcher = QFileSystemWatcher([str(session_dir)], self)
        self._command_watcher.directoryChanged.connect(self._poll_command)

        self._parent_timer = QTimer(self)
        self._parent_timer.timeout.connect(self._check_parent)
        self._parent_timer.start(PARENT_POLL_INTERVAL_MS)

        self._emit_event("ready", pid=os.getpid())

    def _add_action(self, label: str, action_name: str) -> None:
        action = QAction(label, self)
        action.triggered.connect(
            lambda _checked=False, name=action_name: self._emit_event(name)
        )
        self._menu.addAction(action)

    def _emit_event(self, event: str, **details: Any) -> None:
        self._event_serial += 1
        _write_json_atomic(
            self._event_path,
            {"serial": self._event_serial, "event": event, **details},
        )

    def _tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self._emit_event("show")

    def _poll_command(self, _changed_directory: str = "") -> None:
        try:
            payload = json.loads(self._command_path.read_text(encoding="utf-8"))
            serial = int(payload.get("serial", 0))
        except (
            FileNotFoundError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return
        if serial <= self._last_command_serial:
            return
        self._last_command_serial = serial
        if payload.get("command") == "quit":
            self._tray_icon.hide()
            QApplication.quit()

    def _check_parent(self) -> None:
        if not _process_exists(self._parent_pid):
            self._tray_icon.hide()
            QApplication.quit()


def run_tray_process(session_dir: Path, parent_pid: int) -> int:
    app = cast(QApplication | None, QApplication.instance())
    if app is None:
        app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    session_dir.mkdir(parents=True, exist_ok=True)
    service = TrayService(session_dir, parent_pid)
    setattr(app, "_capswriter_tray_service", service)
    return app.exec()


def main() -> int:
    parser = argparse.ArgumentParser(description="CapsWriter tray process")
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args()
    return run_tray_process(args.session_dir, args.parent_pid)


if __name__ == "__main__":
    raise SystemExit(main())
