from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(f"{path.suffix}.tmp")
    temporary_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    temporary_path.replace(path)


class TrayProcessClient:
    """Own and receive actions from the persistent tray GUI process."""

    def __init__(self, root: Path, python_executable: str) -> None:
        self._root = root
        self._python_executable = python_executable
        self._session_dir = Path(tempfile.mkdtemp(prefix="capswriter-tray-"))
        self._command_path = self._session_dir / "command.json"
        self._event_path = self._session_dir / "event.json"
        self._process: subprocess.Popen[str] | None = None
        self._command_serial = 0
        self._last_event_serial = 0

    @property
    def process(self) -> subprocess.Popen[str] | None:
        return self._process

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _build_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            return [
                sys.executable,
                "--tray-process",
                str(self._session_dir),
                "--tray-parent-pid",
                str(os.getpid()),
            ]
        return [
            self._python_executable,
            "-m",
            "src.gui.tray_process",
            str(self._session_dir),
            "--parent-pid",
            str(os.getpid()),
        ]

    def start(self) -> bool:
        if self.is_running():
            return True
        self._last_event_serial = 0
        try:
            self._event_path.unlink(missing_ok=True)
        except OSError:
            pass
        try:
            self._process = subprocess.Popen(
                self._build_command(),
                cwd=str(self._root),
                env=os.environ.copy(),
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except OSError:
            self._process = None
            return False
        return True

    def poll_event(self) -> dict[str, Any] | None:
        try:
            payload = json.loads(self._event_path.read_text(encoding="utf-8"))
            serial = int(payload.get("serial", 0))
        except (
            FileNotFoundError,
            OSError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            return None
        if serial <= self._last_event_serial:
            return None
        self._last_event_serial = serial
        return payload

    def stop(self) -> None:
        process = self._process
        if process is not None and process.poll() is None:
            self._command_serial += 1
            try:
                _write_json_atomic(
                    self._command_path,
                    {"serial": self._command_serial, "command": "quit"},
                )
                process.wait(timeout=1.0)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    process.kill()
        self._process = None
        shutil.rmtree(self._session_dir, ignore_errors=True)
