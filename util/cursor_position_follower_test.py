import sys
import win32api
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtGui import QPalette, QFont, QColor, QGuiApplication

class Cursor_Position(QLabel):
    def __init__(self):
        super().__init__()

        self.setWindowFlags(Qt.ToolTip | Qt.FramelessWindowHint)
        self.resize(150, 20)
        font = QFont("Segoe MDL2 Assets", 14)
        self.setFont(font)
        palette = self.palette()
        palette.setColor(QPalette.Window, QColor("#212121"))  # 设置背景颜色
        palette.setColor(QPalette.WindowText, QColor("#00B294"))  # 设置文本颜色
        self.setPalette(palette)

        # 创建一个定时器来定期更新鼠标位置
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_tooltip_position)
        self.timer.start(100)  # 每100毫秒更新一次

    def update_tooltip_position(self):
        # 使用pywin32获取全局鼠标位置
        x, y = win32api.GetCursorPos()
        global scale_x, scale_y
        print(f"屏幕缩放比例: {scale_x}, {scale_y}")
        x, y = x / scale_x, y / scale_y
        print(x, y)
        # 更新标签的位置和文本
        self.move(x+(20/scale_x),y+(20/scale_y))
        self.setText(f"X: {int(x)}, Y: {int(y)}")
        self.setVisible(True)

def Print_Screen_Scale():
    """打印屏幕信息（多显示器友好）。"""
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        raise RuntimeError("No primary screen available")
    vrect = screen.virtualGeometry()
    logical_width = int(vrect.width())
    logical_height = int(vrect.height())
    print(f"逻辑尺寸(虚拟桌面): {logical_width}x{logical_height}")

    dpi_x = float(getattr(screen, "logicalDotsPerInchX", lambda: screen.logicalDotsPerInch())())
    dpi_y = float(getattr(screen, "logicalDotsPerInchY", lambda: screen.logicalDotsPerInch())())

    global scale_x, scale_y
    scale_x = dpi_x / 96.0 if dpi_x else 1.0
    scale_y = dpi_y / 96.0 if dpi_y else 1.0
    print(f"主屏缩放比例: {scale_x:.2f}, {scale_y:.2f}")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    Print_Screen_Scale()
    window = Cursor_Position()
    window.setWindowTitle("Cursor Position Follower")
    window.show()
    sys.exit(app.exec())






