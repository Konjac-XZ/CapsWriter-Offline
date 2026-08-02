from __future__ import annotations

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from src.gui.tray_menu import AutoDismissTrayMenu


def test_visible_tray_menu_closes_when_window_deactivates() -> None:
    app = QApplication.instance() or QApplication([])
    menu = AutoDismissTrayMenu()
    menu.show()
    assert menu.isVisible()

    QCoreApplication.sendEvent(menu, QEvent(QEvent.Type.WindowDeactivate))

    assert not menu.isVisible()
    menu.deleteLater()
    app.processEvents()


def test_hidden_tray_menu_ignores_window_deactivation() -> None:
    app = QApplication.instance() or QApplication([])
    menu = AutoDismissTrayMenu()

    QCoreApplication.sendEvent(menu, QEvent(QEvent.Type.WindowDeactivate))

    assert not menu.isVisible()
    menu.deleteLater()
    app.processEvents()
