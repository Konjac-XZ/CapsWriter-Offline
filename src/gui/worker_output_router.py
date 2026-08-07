import json
import threading
import time
from dataclasses import dataclass
from queue import Queue
from typing import TextIO

from PySide6.QtCore import QObject, Signal


GUI_MARKER = "CW_GUI:"
OVERLAY_EVENTS = {"status_overlay", "listening_overlay"}


@dataclass(frozen=True)
class WorkerLogLine:
    text: str
    color: str | None = None


class WorkerOutputRouter(QObject):
    overlay_event = Signal(dict)
    context_event = Signal(dict)
    daily_input_count_event = Signal(int)
    output_available = Signal()

    def __init__(self):
        super().__init__()
        self._log_queue: Queue[WorkerLogLine] = Queue()
        self._level_lock = threading.Lock()
        self._latest_overlay_level: float | None = None
        self._notification_lock = threading.Lock()
        self._notification_pending = False

    def read_stream(self, stream: TextIO | None) -> None:
        if stream is None:
            return
        for line in iter(stream.readline, ""):
            self.route_line(line.strip())

    def route_line(self, line: str) -> None:
        if not line:
            return
        payload = self._parse_gui_payload(line)
        if payload is None:
            self._log_queue.put(WorkerLogLine(line))
            self._notify_output_available()
            return

        event = payload.get("event")
        if event in OVERLAY_EVENTS:
            self._route_overlay_event(payload)
            return
        if event == "context_toggle":
            self.context_event.emit(payload)
            return
        if event == "daily_input_count":
            try:
                count = max(0, int(payload.get("count", 0)))
            except (TypeError, ValueError):
                return
            self.daily_input_count_event.emit(count)
            return

        self._log_queue.put(
            WorkerLogLine(
                text=str(payload.get("text", "")),
                color=(str(payload["color"]) if payload.get("color") else None),
            )
        )
        self._notify_output_available()

    def consume_output_notification(self) -> None:
        """Allow one new wakeup while the GUI drains queued worker output."""
        with self._notification_lock:
            self._notification_pending = False

    def _notify_output_available(self) -> None:
        with self._notification_lock:
            if self._notification_pending:
                return
            self._notification_pending = True
        self.output_available.emit()

    def notify_if_output_remains(self) -> None:
        """Schedule another bounded GUI drain when a backlog remains."""
        with self._level_lock:
            has_level = self._latest_overlay_level is not None
        if has_level or not self._log_queue.empty():
            self._notify_output_available()

    def take_latest_overlay_level(self) -> float | None:
        with self._level_lock:
            level = self._latest_overlay_level
            self._latest_overlay_level = None
        return level

    def take_log_lines(self, max_lines: int) -> list[WorkerLogLine]:
        lines: list[WorkerLogLine] = []
        while len(lines) < max_lines and not self._log_queue.empty():
            lines.append(self._log_queue.get())
        return lines

    def take_log_lines_for(
        self, max_lines: int, max_seconds: float
    ) -> list[WorkerLogLine]:
        lines: list[WorkerLogLine] = []
        deadline = time.perf_counter() + max(0.0, max_seconds)
        while len(lines) < max_lines and not self._log_queue.empty():
            if lines and time.perf_counter() >= deadline:
                break
            lines.append(self._log_queue.get())
        return lines

    def _route_overlay_event(self, payload: dict) -> None:
        if payload.get("action") == "level":
            try:
                level = float(payload.get("level", 0.0))
            except Exception:
                level = 0.0
            with self._level_lock:
                self._latest_overlay_level = max(0.0, min(1.0, level))
            self._notify_output_available()
            return

        with self._level_lock:
            self._latest_overlay_level = None
        self.overlay_event.emit(payload)

    def _parse_gui_payload(self, line: str) -> dict | None:
        if not line.startswith(GUI_MARKER):
            return None
        try:
            payload = json.loads(line[len(GUI_MARKER) :])
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None
