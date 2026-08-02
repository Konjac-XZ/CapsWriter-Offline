from __future__ import annotations

import argparse
import ctypes
import json
import os
import sys
from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import QObject, QTimer
from PySide6.QtWidgets import QApplication, QDialog

from src.gui.lexicon_editor import LexiconEditDialog, read_lexicon_text


COMMAND_POLL_INTERVAL_MS = 100
PARENT_POLL_INTERVAL_MS = 2000


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary_path.replace(path)


def _windows_process_exists(pid: int) -> bool:
    """Check a Windows process without sending it a signal.

    Unlike POSIX, ``os.kill(pid, 0)`` is not a harmless existence probe on
    Windows: CPython may implement it via TerminateProcess.  A waitable process
    handle gives us a read-only liveness check instead.
    """
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


class LexiconEditorService(QObject):
    """Own the Monaco dialog in a GUI process separate from CapsWriter."""

    def __init__(self, session_dir: Path, parent_pid: int) -> None:
        super().__init__()
        self._session_dir = session_dir
        self._command_path = session_dir / "command.json"
        self._event_path = session_dir / "event.json"
        self._parent_pid = parent_pid
        self._last_command_serial = 0
        self._active_serial: int | None = None
        self._pending_show_serial: int | None = None
        self._dialog: LexiconEditDialog | None = None
        self._dialog_ready = False

        self._command_timer = QTimer(self)
        self._command_timer.timeout.connect(self._poll_command)
        self._command_timer.start(COMMAND_POLL_INTERVAL_MS)

        self._parent_timer = QTimer(self)
        self._parent_timer.timeout.connect(self._check_parent)
        self._parent_timer.start(PARENT_POLL_INTERVAL_MS)

    def _initialize_dialog(self) -> None:
        """Create Monaco only after the first explicit show request."""
        if self._dialog is not None:
            return
        try:
            # Construction can be expensive, but it happens in this isolated
            # GUI process and cannot block the main CapsWriter event loop.
            dialog = LexiconEditDialog(initial_text="")
            dialog.hide()
            dialog.initialized.connect(self._dialog_initialized)
            dialog.finished.connect(self._dialog_finished)
            self._dialog = dialog
        except Exception as exc:
            serial = self._pending_show_serial or self._last_command_serial
            self._pending_show_serial = None
            _write_json_atomic(
                self._event_path,
                {"serial": serial, "event": "error", "message": str(exc)},
            )

    def _dialog_initialized(self) -> None:
        self._dialog_ready = True
        serial = self._pending_show_serial
        self._pending_show_serial = None
        if serial is not None:
            self._open_dialog(serial)

    def _open_dialog(self, serial: int) -> None:
        dialog = self._dialog
        if dialog is None:
            return
        try:
            dialog.prepare_for_open(read_lexicon_text())
        except Exception as exc:
            _write_json_atomic(
                self._event_path,
                {"serial": serial, "event": "error", "message": str(exc)},
            )
            return

        self._active_serial = serial
        dialog.open()
        dialog.raise_()
        dialog.activateWindow()
        _write_json_atomic(
            self._event_path,
            {"serial": serial, "event": "opened"},
        )

    def _poll_command(self) -> None:
        try:
            payload = json.loads(self._command_path.read_text(encoding="utf-8"))
            serial = int(payload.get("serial", 0))
        except (FileNotFoundError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return

        if serial <= self._last_command_serial:
            return
        self._last_command_serial = serial

        command = payload.get("command")
        if command == "quit":
            QApplication.quit()
            return
        if command != "show":
            return

        dialog = self._dialog
        if dialog is not None and dialog.isVisible():
            dialog.raise_()
            dialog.activateWindow()
            _write_json_atomic(
                self._event_path,
                {"serial": serial, "event": "already_open"},
            )
            return

        if dialog is None:
            self._pending_show_serial = serial
            QTimer.singleShot(0, self._initialize_dialog)
            return
        if not self._dialog_ready:
            self._pending_show_serial = serial
            return
        self._open_dialog(serial)

    def _dialog_finished(self, result: int) -> None:
        serial = self._active_serial
        self._active_serial = None
        if serial is None:
            return
        accepted = result == int(QDialog.DialogCode.Accepted)
        _write_json_atomic(
            self._event_path,
            {
                "serial": serial,
                "event": "saved" if accepted else "cancelled",
            },
        )

    def _check_parent(self) -> None:
        if not _process_exists(self._parent_pid):
            QApplication.quit()


def run_lexicon_editor_process(session_dir: Path, parent_pid: int) -> int:
    app = cast(QApplication | None, QApplication.instance())
    if app is None:
        app = QApplication([sys.argv[0]])
    app.setQuitOnLastWindowClosed(False)
    session_dir.mkdir(parents=True, exist_ok=True)
    service = LexiconEditorService(session_dir, parent_pid)
    setattr(app, "_capswriter_lexicon_service", service)
    return app.exec()


def main() -> int:
    parser = argparse.ArgumentParser(description="CapsWriter Monaco lexicon editor process")
    parser.add_argument("session_dir", type=Path)
    parser.add_argument("--parent-pid", type=int, required=True)
    args = parser.parse_args()
    return run_lexicon_editor_process(args.session_dir, args.parent_pid)


if __name__ == "__main__":
    raise SystemExit(main())
