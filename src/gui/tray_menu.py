from __future__ import annotations

import ctypes
import sys

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import QMenu, QWidget


class AutoDismissTrayMenu(QMenu):
    """A tray menu that reliably dismisses when another window is activated.

    A tray-only application may have no foreground top-level window when Qt
    opens its context menu.  On Windows that can prevent the popup from
    receiving the deactivation notification which normally closes a QMenu.
    Giving the popup foreground ownership restores the standard menu behavior.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("trayMenu")

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # Wait until the platform window has been made visible before asking
        # Windows to make it the foreground popup.
        QTimer.singleShot(0, self._activate_popup)

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.WindowDeactivate and self.isVisible():
            self.close()
        return super().event(event)

    def _activate_popup(self) -> None:
        if not self.isVisible():
            return

        self.raise_()
        self.activateWindow()

        if sys.platform != "win32":
            return

        try:
            hwnd = int(self.winId())
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        except (AttributeError, OSError, TypeError, ValueError):
            # Qt's normal popup handling remains the fallback on platforms or
            # Windows environments where foreground activation is unavailable.
            pass
