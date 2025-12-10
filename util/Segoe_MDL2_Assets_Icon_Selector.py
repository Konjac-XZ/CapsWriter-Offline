import sys
import clipman
from PySide6.QtWidgets import QApplication, QMainWindow, QGridLayout, QWidget, QPushButton, QScrollArea
from PySide6.QtGui import QFont, QFontDatabase

class IconBrowser(QMainWindow):
    def __init__(self):
        super().__init__()
        self.initUI()

    def initUI(self):
        self.resize(537, 537)
        # 加载 Segoe MDL2 Assets 字体 
        # 图标列表：https://learn.microsoft.com/zh-cn/windows/apps/design/style/segoe-ui-symbol-font#icon-list

        # Windows 10 之后自带 Segoe MDL2 Assets，或从 https://aka.ms/SegoeFonts 下载

        # font_SegMDL2 = QFontDatabase.addApplicationFont("fonts\Segoe fonts v1710\SegMDL2.ttf") # 0
        # font_segoeui = QFontDatabase.addApplicationFont("fonts\Segoe fonts v1710\segoeui.ttf") # 1
        # font_segoeuib = QFontDatabase.addApplicationFont("fonts\Segoe fonts v1710\segoeuib.ttf") # 2
        # font_segoeuil = QFontDatabase.addApplicationFont("fonts\Segoe fonts v1710\segoeuil.ttf") # 3
        # font_segoeuisl = QFontDatabase.addApplicationFont("fonts\Segoe fonts v1710\segoeuisl.ttf") # 4
        # font_seguisb = QFontDatabase.addApplicationFont("fonts\Segoe fonts v1710\seguisb.ttf") # 5
        # print("加载字体:", font_SegMDL2, font_segoeui, font_segoeuib, font_segoeuil, font_segoeuisl, font_seguisb)
        # fontfamily_SegMDL2 = QFontDatabase.applicationFontFamilies(font_SegMDL2)
        # fontfamily_segoeui = QFontDatabase.applicationFontFamilies(font_segoeui)
        # fontfamily_segoeuib = QFontDatabase.applicationFontFamilies(font_segoeuib)
        # fontfamily_segoeuil = QFontDatabase.applicationFontFamilies(font_segoeuil)
        # fontfamily_segoeuisl = QFontDatabase.applicationFontFamilies(font_segoeuisl)
        # fontfamily_seguisb = QFontDatabase.applicationFontFamilies(font_seguisb)
        # print(fontfamily_SegMDL2, fontfamily_segoeui, fontfamily_segoeuib, fontfamily_segoeuil, fontfamily_segoeuisl, fontfamily_seguisb)
        
        # ['Segoe MDL2 Assets'] ['Segoe UI'] ['Segoe UI'] ['Segoe UI Light'] ['Segoe UI Semilight'] ['Segoe UI Semibold']

        # 创建一个中央滚动区域
        scroll_area = QScrollArea(self)
        self.setCentralWidget(scroll_area)

        # 创建一个内容 widget
        content_widget = QWidget()
        scroll_area.setWidget(content_widget)
        scroll_area.setWidgetResizable(True)

        # 创建一个网格布局
        grid_layout = QGridLayout(content_widget)

        # 创建一个 QFont 对象
        # font = QFont(fontfamily_SegMDL2) 
        font = QFont("Segoe MDL2 Assets") # Windows 10 之后自带 Segoe MDL2 Assets
        font.setPointSize(40)
        # 定义每列的 Unicode 编码范围
        ranges = [
            (0xE700, 0xE900),
            (0xEA00, 0xEC00),
            (0xED00, 0xEF00),
            (0xF000, 0xF200),
            (0xF300, 0xF500),
            (0xF600, 0xF800)
        ]

        # 遍历每个 Unicode 编码范围，为每个图标创建一个按钮
        for col, (start, end) in enumerate(ranges):
            for unicode_val in range(start, end + 1):
                char = chr(unicode_val)
                button = QPushButton(char)
                button.setFixedSize(80, 80)
                button.setFont(font)
                # 创建一个额外的函数，将当前循环的unicode_val传递给lambda
                def create_callback(unicode_val):
                    return lambda: self.copy_to_clipboard(unicode_val)
                button.clicked.connect(create_callback(unicode_val))
                grid_layout.addWidget(button, unicode_val - start, col)

        self.setWindowTitle("Segoe MDL2 Assets Icon Selector")
    def copy_to_clipboard(self, unicode_val):
        # 保存剪切板
        try:
            # 初始化剪贴板模块
            clipman.init()
            clipman.set(f"0x{unicode_val}")
            print(f"Character '0x{unicode_val}' copied to clipboard.")
        except clipman.exceptions.ClipmanBaseException as e:
            temp = e
            print(e)
if __name__ == "__main__":
    app = QApplication(sys.argv)
    browser = IconBrowser()
    browser.show()
    sys.exit(app.exec())
