import time
from typing import Callable

from PySide6.QtCore import QPoint, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QPainter, QPainterPath, QScreen
from PySide6.QtWidgets import QApplication, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget


LONGEST_STATUS_PREFIX = "正在监听"
MAX_TIMER_TEXT = "999.9s"
STATUS_PREFIXES = ("正在监听", "转录中", "润色中")


class LevelMeterFrame(QFrame):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._meter_enabled = False
        self._target_level = 0.0
        self._display_level = 0.0
        self._animation_timer = QTimer(self)
        self._animation_timer.timeout.connect(self._tick_level_animation)
        self._animation_timer.setInterval(50)

    def set_meter_enabled(self, enabled: bool) -> None:
        self._meter_enabled = enabled
        if not enabled:
            self._target_level = 0.0
            self._display_level = 0.0
            self._animation_timer.stop()
        elif not self._animation_timer.isActive():
            self._animation_timer.start()
        self.update()

    def set_level(self, level: float) -> None:
        self._target_level = max(0.0, min(1.0, float(level)))
        if not self._meter_enabled:
            self._target_level = 0.0
            return
        if not self._animation_timer.isActive():
            self._animation_timer.start()

    def _tick_level_animation(self) -> None:
        if not self._meter_enabled:
            self._animation_timer.stop()
            return
        target = self._target_level
        if target > self._display_level:
            self._display_level = self._display_level * 0.78 + target * 0.22
        else:
            self._display_level = self._display_level * 0.86 + target * 0.14
        if abs(self._display_level - target) < 0.002:
            self._display_level = target
        self.update()
        if self._display_level == target and target <= 0.001:
            self._animation_timer.stop()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        radius = 18.0

        pane_path = QPainterPath()
        pane_path.addRoundedRect(rect, radius, radius)

        painter.fillPath(pane_path, QColor(20, 24, 28, 230))

        if self._meter_enabled and self._display_level > 0.001:
            fill_rect = QRectF(rect)
            fill_rect.setWidth(rect.width() * self._display_level)
            painter.save()
            painter.setClipPath(pane_path)
            painter.fillRect(fill_rect, QColor(255, 255, 255, 46))
            painter.restore()

        painter.setPen(QColor(255, 255, 255, 55))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(pane_path)


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
        self._abandon_callback: Callable[[], None] | None = None
        self.setObjectName("statusOverlay")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.status_pane = LevelMeterFrame(self)
        self.status_pane.setObjectName("statusPane")
        pane_layout = QHBoxLayout(self.status_pane)
        pane_layout.setContentsMargins(18, 10, 18, 10)
        pane_layout.setSpacing(0)

        label_style = (
            "QLabel {"
            "color: #ffffff;"
            "background: transparent;"
            "font-size: 14px;"
            "font-weight: 600;"
            "}"
        )

        self.prefix_label = QLabel(self._text_prefix, self)
        self.prefix_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        self.prefix_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.prefix_label.setStyleSheet(label_style)
        pane_layout.addWidget(self.prefix_label)

        self.timer_label = QLabel(self._format_elapsed(0), self)
        self.timer_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.timer_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.timer_label.setStyleSheet(label_style)
        pane_layout.addWidget(self.timer_label)

        layout.addWidget(self.status_pane)

        self.abandon_button = QPushButton("×", self)
        self.abandon_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.abandon_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.abandon_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.abandon_button.setFixedSize(38, 38)
        self.abandon_button.setToolTip("放弃当前任务")
        self.abandon_button.setStyleSheet(
            "QPushButton {"
            "color: #ffffff;"
            "background: rgba(20, 24, 28, 230);"
            "border: 1px solid rgba(255, 255, 255, 55);"
            "border-radius: 18px;"
            "font-size: 16px;"
            "font-weight: 600;"
            "line-height: 22px;"
            "padding: 0;"
            "}"
            "QPushButton:hover {"
            "background: rgba(214, 64, 64, 235);"
            "border-color: rgba(255, 210, 210, 190);"
            "}"
            "QPushButton:pressed {"
            "background: rgba(176, 42, 42, 238);"
            "border-color: rgba(255, 225, 225, 210);"
            "}"
            "QPushButton:disabled {"
            "color: #ffffff;"
            "background: rgba(20, 24, 28, 230);"
            "border-color: rgba(255, 255, 255, 55);"
            "}"
        )
        self.abandon_button.clicked.connect(self._request_abandon)
        layout.addWidget(self.abandon_button)

        self._reserve_stable_width()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._update_elapsed)

    def paintEvent(self, event) -> None:
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
        self.status_pane.set_meter_enabled(False)
        self.hide()
        self.timer_label.setText(self._format_elapsed(0))
        self.abandon_button.setEnabled(True)

    def last_position(self) -> QPoint | None:
        if self._last_pos is None:
            return None
        return QPoint(self._last_pos)

    def last_screen(self) -> QScreen | None:
        return self._last_screen

    def set_text_prefix(self, text_prefix: str) -> None:
        self._text_prefix = text_prefix
        self.prefix_label.setText(text_prefix)
        self.timer_label.setText(self._format_elapsed(0))
        self.abandon_button.setEnabled(True)
        self._reserve_stable_width()
        self.adjustSize()

    def set_abandon_callback(self, callback: Callable[[], None] | None) -> None:
        self._abandon_callback = callback

    def set_level(self, level: float) -> None:
        if self._state == "listening" and self.isVisible():
            self.status_pane.set_level(level)

    def show_state(self, state: str) -> None:
        if state not in self._STATE_LABELS:
            self.hide_overlay()
            return
        previous_state = self._state
        self._state = state
        self.set_text_prefix(self._STATE_LABELS[state])
        self.status_pane.set_meter_enabled(state == "listening")
        if self.isVisible():
            self._reset_elapsed()
            if previous_state == "listening" and state != "listening":
                self._move_to_last_screen_bottom()
            self.raise_()
            self._timer.start(100)
            return
        self.show_for_current_cursor_screen()

    def _show_overlay(self) -> None:
        self.abandon_button.setEnabled(True)
        self.show()
        self.raise_()
        self._timer.start(100)

    def _reset_elapsed(self) -> None:
        self._started_at = time.monotonic()
        self._update_elapsed()
        self.adjustSize()

    def _update_elapsed(self) -> None:
        elapsed = max(0.0, time.monotonic() - self._started_at)
        self.timer_label.setText(self._format_elapsed(elapsed))

    def _format_elapsed(self, elapsed: float) -> str:
        return f" {min(elapsed, 999.9):5.1f}s"

    def _reserve_stable_width(self) -> None:
        self.prefix_label.ensurePolished()
        self.timer_label.ensurePolished()
        prefix_metrics = self.prefix_label.fontMetrics()
        timer_metrics = self.timer_label.fontMetrics()
        prefix_width = max(prefix_metrics.horizontalAdvance(prefix) for prefix in STATUS_PREFIXES)
        timer_width = timer_metrics.horizontalAdvance(f" {MAX_TIMER_TEXT}")

        # Leave a small cushion for platform font fallback and bold text rendering.
        self.prefix_label.setFixedWidth(prefix_width + 6)
        self.timer_label.setFixedWidth(timer_width + 6)
        pane_width = self.prefix_label.width() + self.timer_label.width() + 36
        pane_height = max(self.abandon_button.height(), self.status_pane.sizeHint().height())
        self.status_pane.setFixedWidth(pane_width)
        self.status_pane.setFixedHeight(pane_height)
        self.setFixedWidth(pane_width + 6 + self.abandon_button.width())
        self.setFixedHeight(pane_height)

    def _request_abandon(self) -> None:
        self.abandon_button.setEnabled(False)
        if self._abandon_callback is not None:
            self._abandon_callback()

    def _move_to_current_cursor_screen(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        cursor_pos = QCursor.pos()
        screen = QGuiApplication.screenAt(cursor_pos)
        if screen is None:
            screen = QGuiApplication.primaryScreen()
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

    def set_abandon_callback(self, callback: Callable[[], None] | None) -> None:
        self.overlay.set_abandon_callback(callback)

    def show_listening(self) -> None:
        self.overlay.show_state("listening")

    def show_processing(self, state: str) -> None:
        self.overlay.show_state(state)

    def set_level(self, level: float) -> None:
        self.overlay.set_level(level)

    def hide_all(self) -> None:
        self.overlay.hide_overlay()


ListeningOverlay = StatusOverlay
ProcessingOverlay = StatusOverlay
