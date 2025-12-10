import sys

import keyboard
import win32api
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication, QLabel

from util.config import ClientConfig as Config


class Hint_While_Recording_At_Cursor_Position(QLabel):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        font = QFont("Segoe MDL2 Assets", 14)
        self.setFont(font)
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor("#212121"))  # 设置背景颜色
        palette.setColor(QPalette.WindowText, QColor("#00B294"))  # 设置文本颜色
        self.setPalette(palette)
        self.setVisible(False)  # 初始时隐藏标签

        # 创建一个定时器来定期更新鼠标位置
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_tooltip_position)
        self.timer.start(100)  # 每100毫秒更新一次

    def update_tooltip_position(self):
        # 使用pywin32获取全局鼠标位置
        x, y = win32api.GetCursorPos()
        # 更新标签的位置和文本
        self.move(x + 20, y + 20)
        if keyboard.is_pressed(Config.speech_recognition_shortcut):
            self.setText(chr(0xF8B1))
            self.setVisible(True)
        else:
            self.setVisible(False)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    tooltip = Hint_While_Recording_At_Cursor_Position()
    tooltip.show()  # 显示标签
    sys.exit(app.exec())
