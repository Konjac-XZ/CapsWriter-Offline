import time

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPen, QScreen
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QSizePolicy, QWidget


LONGEST_STATUS_PREFIX = "正在监听"
MAX_TIMER_TEXT = "999.9s"
STATUS_PREFIXES = ("正在监听", "转录中", "润色中")


class StatusOverlay(QWidget):
    _STATE_LABELS = {
        "listening": "正在监听",
        "transcribing": "转录中",
        "polishing": "润色中",
    }

    def __init__(self):
        super().__init__(
            None,
            Qt.WindowType.Tool
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowDoesNotAcceptFocus
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self._state = "listening"
        self._text_prefix = self._STATE_LABELS[self._state]
        self._started_at = 0.0
        self._bottom_offset = 80
        self._last_pos: QPoint | None = None
        self._last_screen: QScreen | None = None
        self.setObjectName("statusOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(0)

        self.label = QLabel(self._format_text(0), self)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.label.setStyleSheet(
            "QLabel {"
            "color: #ffffff;"
            "background: transparent;"
            "font-size: 14px;"
            "font-weight: 600;"
            "}"
        )
        layout.addWidget(self.label)
        self._reserve_stable_width()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_elapsed)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)
        painter.setPen(QPen(QColor(255, 255, 255, 55), 1))
        painter.setBrush(QColor(20, 24, 28, 230))
        painter.drawRoundedRect(rect, 18, 18)
        super().paintEvent(event)

    def show_for_current_cursor_screen(self) -> None:
        self._reset_elapsed()
        self._move_to_current_cursor_screen()
        self._show_overlay()

    def show_at_position(
        self,
        position: QPoint | None,
        screen: QScreen | None = None,
    ) -> None:
        self._reset_elapsed()
        self.adjustSize()
        if position is None:
            self._move_to_current_cursor_screen()
        else:
            if screen is not None:
                try:
                    self.setScreen(screen)
                except Exception:
                    pass
                self._last_screen = screen
            self.move(position)
            self._last_pos = QPoint(position)
        self._show_overlay()

    def show_for_screen(self, screen: QScreen | None) -> None:
        self._reset_elapsed()
        if screen is None:
            self._move_to_current_cursor_screen()
        else:
            self._move_to_screen_bottom(screen)
        self._show_overlay()

    def hide_overlay(self) -> None:
        self._timer.stop()
        self.hide()
        self.label.setText(self._format_text(0))

    def last_position(self) -> QPoint | None:
        if self._last_pos is None:
            return None
        return QPoint(self._last_pos)

    def last_screen(self) -> QScreen | None:
        return self._last_screen

    def set_text_prefix(self, text_prefix: str) -> None:
        self._text_prefix = text_prefix
        self.label.setText(self._format_text(0))
        self._reserve_stable_width()
        self.adjustSize()

    def show_state(self, state: str) -> None:
        if state not in self._STATE_LABELS:
            self.hide_overlay()
            return
        previous_state = self._state
        self._state = state
        self.set_text_prefix(self._STATE_LABELS[state])
        if self.isVisible():
            self._reset_elapsed()
            if previous_state == "listening" and state != "listening":
                self._move_to_last_screen_bottom()
            self.raise_()
            self._timer.start(100)
            return
        self.show_for_current_cursor_screen()

    def _show_overlay(self) -> None:
        self.show()
        self.raise_()
        self._timer.start(100)

    def _reset_elapsed(self) -> None:
        self._started_at = time.monotonic()
        self._update_elapsed()
        self.adjustSize()

    def _update_elapsed(self) -> None:
        elapsed = max(0.0, time.monotonic() - self._started_at)
        self.label.setText(self._format_text(elapsed))
        if self.isVisible():
            old_size = self.size()
            self.adjustSize()
            if self.size() != old_size:
                self._move_to_current_cursor_screen()

    def _format_text(self, elapsed: float) -> str:
        return f"{self._text_prefix} {min(elapsed, 999.9):5.1f}s"

    def _reserve_stable_width(self) -> None:
        self.label.ensurePolished()
        metrics = self.label.fontMetrics()
        text_width = max(
            metrics.horizontalAdvance(f"{prefix} {MAX_TIMER_TEXT}")
            for prefix in STATUS_PREFIXES
        )
        # Leave a small cushion for platform font fallback and bold text rendering.
        label_width = text_width + 12
        self.label.setFixedWidth(label_width)
        self.setFixedWidth(label_width + 36)

    def _move_to_current_cursor_screen(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        if screen is None:
            screen = app.primaryScreen()
        self._move_to_screen_bottom(screen)

    def _move_to_last_screen_bottom(self) -> None:
        if self._last_screen is not None:
            self._move_to_screen_bottom(self._last_screen)

    def _move_to_screen_bottom(self, screen: QScreen | None) -> None:
        if screen is None:
            return
        try:
            self.setScreen(screen)
        except Exception:
            pass
        self._last_screen = screen
        available = screen.availableGeometry()
        x = available.x() + (available.width() - self.width()) // 2
        y = available.y() + available.height() - self.height() - self._bottom_offset
        pos = QPoint(x, y)
        self.move(pos)
        self._last_pos = QPoint(pos)


class StatusOverlayController:
    def __init__(self):
        self.overlay = StatusOverlay()

    def show_listening(self) -> None:
        self.overlay.show_state("listening")

    def show_processing(self, state: str) -> None:
        self.overlay.show_state(state)

    def hide_all(self) -> None:
        self.overlay.hide_overlay()


ListeningOverlay = StatusOverlay
ProcessingOverlay = StatusOverlay
