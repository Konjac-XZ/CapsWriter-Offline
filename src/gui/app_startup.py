from __future__ import annotations

import os
from typing import Any

from PySide6.QtCore import QLocale, QTimer
from PySide6.QtGui import QFont, QGuiApplication
from PySide6.QtWidgets import QApplication

from src.gui.runtime import theme_css_path


def apply_theme_later(app: QApplication) -> None:
    """Apply qt_material theme after the first paint to improve perceived startup speed."""
    enable_theme = os.getenv("CW_ENABLE_QT_MATERIAL", "0").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if not enable_theme:
        return

    try:
        from qt_material import apply_stylesheet
    except Exception:
        return

    def do_apply() -> None:
        try:
            apply_stylesheet(app, theme="dark_teal.xml", css_file=str(theme_css_path()))
        except Exception:
            pass

    try:
        delay_ms = int(os.getenv("CW_THEME_DELAY_MS", "3000"))
        if delay_ms < 0:
            delay_ms = 0
        QTimer.singleShot(delay_ms, do_apply)
    except Exception:
        try:
            do_apply()
        except Exception:
            pass


def configure_app_locale_and_font(app: QApplication, font_family: str) -> None:
    try:
        QLocale.setDefault(QLocale(QLocale.Language.Chinese, QLocale.Country.China))
    except Exception:
        pass

    try:
        app_font = QFont(font_family)
        qfont_type: Any = QFont
        if hasattr(qfont_type, "StyleStrategy") and hasattr(qfont_type.StyleStrategy, "PreferAntialias"):
            app_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        elif hasattr(qfont_type, "PreferAntialias"):
            app_font.setStyleStrategy(qfont_type.PreferAntialias)

        if hasattr(qfont_type, "HintingPreference") and hasattr(qfont_type.HintingPreference, "PreferFullHinting"):
            app_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        elif hasattr(qfont_type, "PreferFullHinting"):
            app_font.setHintingPreference(qfont_type.PreferFullHinting)
        app.setFont(app_font)
    except Exception as e:
        print(f"Error setting app font: {e}")


def print_screen_scale() -> tuple[float, float]:
    """Print accurate multi-monitor screen information and return primary-screen scale."""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        raise RuntimeError("No primary screen available")

    vrect = screen.virtualGeometry()
    logical_width = int(vrect.width())
    logical_height = int(vrect.height())
    print(f"逻辑尺寸(虚拟桌面): {logical_width}x{logical_height}")

    dpi_x = float(getattr(screen, "logicalDotsPerInchX", lambda: screen.logicalDotsPerInch())())
    dpi_y = float(getattr(screen, "logicalDotsPerInchY", lambda: screen.logicalDotsPerInch())())

    scale_x = dpi_x / 96.0 if dpi_x else 1.0
    scale_y = dpi_y / 96.0 if dpi_y else 1.0
    print(f"主屏缩放比例: {scale_x:.2f}, {scale_y:.2f}")

    est_physical_w = int(round(logical_width * scale_x))
    est_physical_h = int(round(logical_height * scale_y))
    print(f"估算虚拟桌面物理像素(按主屏缩放): {est_physical_w}x{est_physical_h}")

    return scale_x, scale_y
