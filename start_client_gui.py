import argparse
import os
import subprocess
import sys
import threading
from pathlib import Path
from queue import Queue
from dotenv import load_dotenv

load_dotenv()

import win32api
import win32con
import win32gui
import win32print
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QAction, QFont, QIcon, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSpacerItem,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
from qt_material import apply_stylesheet

from util.check_microphone_usage import is_microphone_in_use
from util.check_process import check_process
from util.config import ClientConfig as Config


class Hint_While_Recording_At_Cursor_Position(QLabel):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setVisible(False)  # 初始时隐藏标签

        # 创建一个定时器来定期更新鼠标位置
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_tooltip_position)
        self.timer.start(100)  # 每100毫秒更新一次

    def update_tooltip_position(self):
        # 使用pywin32获取全局鼠标位置
        x, y = win32api.GetCursorPos()
        global scale_x, scale_y
        x, y = x / scale_x, y / scale_y
        # 更新标签的位置和文本
        self.move(x + (20 / scale_x), y + (20 / scale_y))
        if is_microphone_in_use():
            self.setText(chr(0xF8B1))
            self.setVisible(True)
        else:
            self.setVisible(False)


class GUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.init_ui()
        self.output_queue_client = Queue()
        self.start_script()
        self.edgeMargin = 5  # 侧边停靠残余像素值
        self.isBerthLeft = False
        self.isBerthRight = False

    def init_ui(self):
        self.resize(425, 425)
        self.setWindowTitle("CapsWriter-Offline-Client")
        self.setWindowIcon(QIcon("assets/client-icon.ico"))
        self.setWindowOpacity(0.9)
        self.setWindowFlags(
            self.windowFlags()
            | Qt.FramelessWindowHint  # 隐藏标题栏
            | Qt.Tool  # 隐藏Windows任务栏上的图标
            | Qt.WindowStaysOnTopHint  # 置顶
        )
        self.create_stay_on_top_button()
        self.create_cloudypaste_button()  # Create cloudy paste button
        self.create_clear_button()  # Create clear button
        self.create_close_button()
        self.create_custom_title_bar()
        self.create_text_box()
        self.create_monitor_checkbox()  # Create monitor checkbox
        # self.create_stay_on_top_checkbox()
        self.create_wordcount_label()
        self.create_systray_icon()

        # Create a vertical layout
        self.layout = QVBoxLayout()
        self.layout.setSpacing(0)  # 设置控件间距为0像素
        self.layout.setContentsMargins(3, 3, 3, 3)  # 设置左、上、右、下的边距
        self.layout2 = QHBoxLayout()
        self.layout2.setSpacing(0)  # 设置控件间距为0像素
        self.layout2.setContentsMargins(0, 0, 0, 0)  # 设置左、上、右、下的边距为0像素

        # Add text box and button to the layout
        self.layout.addLayout(self.title_bar)
        self.layout.addWidget(self.text_box_client)
        self.layout2.addWidget(self.monitor_checkbox, alignment=Qt.AlignLeft)
        # self.layout2.addWidget(self.stay_on_top_checkbox, alignment=Qt.AlignLeft)
        self.layout2.addSpacerItem(
            QSpacerItem(40, 0, QSizePolicy.Expanding, QSizePolicy.Minimum)
        )
        self.layout2.addWidget(self.text_box_wordCountLabel, alignment=Qt.AlignRight)
        self.layout.addLayout(self.layout2)

        # Create a central widget
        central_widget = QWidget()
        central_widget.setLayout(self.layout)
        # Set the central widget
        self.setCentralWidget(central_widget)

    def create_custom_title_bar(self):
        # 创建自定义标题栏
        self.title_bar = QHBoxLayout()
        self.title_bar.addWidget(self.stay_on_top_button)
        self.title = QLabel("CapsWriter-Offline-Client")
        font = QFont()
        font.setBold(True)
        self.title.setFont(font)
        self.title_bar.addWidget(self.title)
        self.title_bar.addSpacerItem(
            QSpacerItem(80, 0, QSizePolicy.Expanding, QSizePolicy.Minimum)
        )
        self.title_bar.addWidget(self.cloudypaste_button, alignment=Qt.AlignRight)
        self.title_bar.addWidget(self.clear_button, alignment=Qt.AlignRight)
        self.title_bar.addWidget(self.close_button)

    def create_stay_on_top_button(self):
        self.stay_on_top_button = QPushButton()
        pin_char = chr(0xE840)
        self.stay_on_top_button.setText(pin_char)
        self.stay_on_top_button.setToolTip("置顶窗口，将它显示在其他窗口之上 / 不置顶")
        self.stay_on_top_button.setMaximumSize(50, 50)
        self.stay_on_top_button.clicked.connect(self.window_stay_on_top_toggled)

    def create_close_button(self):
        self.close_button = QPushButton(chr(0xE8BB))
        self.close_button.setMaximumSize(50, 50)
        self.close_button.clicked.connect(self.hide)

    def create_text_box(self):
        self.text_box_client = QTextEdit()
        self.text_box_client.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_box_client.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

    def create_monitor_checkbox(self):
        # 创建一个QCheckBox控件
        self.monitor_checkbox = QCheckBox("监听")
        self.monitor_checkbox.setToolTip("监听客户端输出 / 不监听，仅用作笔记本")
        self.monitor_checkbox.setMaximumSize(65, 30)
        # 当状态改变时，调用self.on_monitor_toggled函数
        self.monitor_checkbox.stateChanged.connect(self.on_monitor_toggled)
        # 设置默认状态（初始化时不触发信号）
        self.monitor_checkbox.blockSignals(True)
        self.monitor_checkbox.setChecked(True)
        self.monitor_checkbox.blockSignals(False)

    # def create_stay_on_top_checkbox(self):
    #     self.stay_on_top_checkbox = QCheckBox('置顶')
    #     self.stay_on_top_checkbox.setToolTip("置顶窗口，将它显示在其他窗口之上 / 不置顶")
    #     self.stay_on_top_checkbox.setMaximumSize(65, 30)
    #     self.stay_on_top_checkbox.stateChanged.connect(self.window_stay_on_top_toggled)
    #     self.stay_on_top_checkbox.setChecked(True)

    def create_wordcount_label(self):
        self.text_box_wordCountLabel = QLabel("字符数字节数", self)
        self.text_box_wordCountLabel.setToolTip("光标已选中字符数 / 总字符数 | 字节数")
        self.text_box_wordCountLabel.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.text_box_client.textChanged.connect(self.update_word_count_toggled)
        self.text_box_client.selectionChanged.connect(self.update_word_count_toggled)

    def create_cloudypaste_button(self):
        self.cloudypaste_button = QPushButton(chr(0xE753), self)
        self.cloudypaste_button.setToolTip(
            "将文本上传至云剪切板，方便向ios设备分享。基于 cv.j20.cc ，一个无依赖即用即走的剪切板。实测5~1024字节，不足字节补.超出字节无效。"
        )
        self.cloudypaste_button.setMaximumSize(80, 30)
        self.cloudypaste_button.clicked.connect(self.cloudy_paste)

    def create_clear_button(self):
        # Create a button
        self.clear_button = QPushButton(chr(0xE75C), self)
        self.clear_button.setToolTip("清空文本框中的全部内容")
        self.clear_button.setMaximumSize(80, 30)
        # Connect click event
        self.clear_button.clicked.connect(lambda: self.clear_text_box())

    def create_systray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon("assets/client-icon.ico"))
        edit_hot_en_action = QAction("Edit hot-en.txt", self)
        edit_hot_rule_action = QAction("Edit hot-rule.txt", self)
        edit_hot_zh_action = QAction("Edit hot-zh.txt", self)
        edit_keyword_action = QAction("Edit keywords.txt", self)

        explore_home_folder_action = QAction("📁 Open Home Folder With Explorer", self)
        vscode_home_folder_action = QAction("🤓 Open Home Folder With VSCode", self)
        chatglm_website_action = QAction("🤖 ChatGLM Website", self)

        github_website_action = QAction("🌐 GitHub Website", self)
        show_action = QAction("🪟 Show", self)
        restart_client_action = QAction("🔄 Restart Client", self)
        quit_action = QAction("❌ Quit", self)

        edit_hot_en_action.triggered.connect(self.edit_hot_en)
        edit_hot_rule_action.triggered.connect(self.edit_hot_rule)
        edit_hot_zh_action.triggered.connect(self.edit_hot_zh)
        edit_keyword_action.triggered.connect(self.edit_keyword)

        explore_home_folder_action.triggered.connect(self.explore_home_folder)
        vscode_home_folder_action.triggered.connect(self.vscode_home_folder)
        chatglm_website_action.triggered.connect(self.open_chatglm_website)

        github_website_action.triggered.connect(self.open_github_website)
        show_action.triggered.connect(self.showNormal)
        restart_client_action.triggered.connect(self.restart_client)
        quit_action.triggered.connect(self.quit_app)

        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        tray_menu = QMenu()
        edit_menu = QMenu("📝 Edit Hot Rules", tray_menu)
        view_menu = QMenu("👁️ View", tray_menu)

        edit_menu.addAction(edit_hot_en_action)
        edit_menu.addAction(edit_hot_rule_action)
        edit_menu.addAction(edit_hot_zh_action)
        edit_menu.addAction(edit_keyword_action)

        view_menu.addAction(explore_home_folder_action)
        view_menu.addAction(vscode_home_folder_action)
        view_menu.addAction(chatglm_website_action)

        tray_menu.addMenu(edit_menu)
        tray_menu.addMenu(view_menu)

        tray_menu.addAction(github_website_action)
        tray_menu.addSeparator()
        tray_menu.addAction(show_action)
        tray_menu.addAction(restart_client_action)
        tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(tray_menu)
        self.tray_icon.show()

    def restart_client(self):
        subprocess.Popen(
            [".\\runtime\\python.exe", ".\\util\\client_restart.py"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )

    def cloudy_paste(self):
        text = self.text_box_client.toPlainText()
        subprocess.Popen(
            [
                ".\\runtime\\pythonw.exe",
                ".\\util\\cloud_clipboard_show_qrcode.py",
                text,
            ],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )

    def clear_text_box(self):
        # Clear the content of the client text box
        self.text_box_client.clear()
        # Resize Window
        self.resize(425, 425)

    def on_monitor_toggled(self, state):
        # 检查复选框的选中状态
        timer = getattr(self, "update_timer", None)
        if timer is None:
            return
        if state == 2:  # 2 表示选中状态
            timer.start(100)
        else:
            timer.stop()

    # def window_stay_on_top_toggled(self):
    #     # 切换窗口置顶状态
    #     if self.windowFlags() & Qt.WindowStaysOnTopHint:
    #         self.setWindowFlags(self.windowFlags() ^ Qt.WindowStaysOnTopHint)
    #     else:
    #         self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
    #     self.show()  # 重新显示窗口以应用更改
    def window_stay_on_top_toggled(self):
        # 切换窗口置顶状态
        if self.windowFlags() & Qt.WindowStaysOnTopHint:
            self.setWindowFlags(self.windowFlags() ^ Qt.WindowStaysOnTopHint)
        else:
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)
            global gui
        window_is_on_top = bool(gui.windowFlags() & Qt.WindowStaysOnTopHint)
        if window_is_on_top:
            pin_char = chr(0xE840)
            self.stay_on_top_button.setText(pin_char)
        else:
            unpin_char = " "
            self.stay_on_top_button.setText(unpin_char)
        self.show()  # 重新显示窗口以应用更改

    def update_word_count_toggled(self):
        select_text_count = len(self.text_box_client.textCursor().selectedText())
        select_text_bytes = len(
            self.text_box_client.textCursor().selectedText().encode("utf-8")
        )
        total_text_count = len(self.text_box_client.toPlainText())
        total_text_bytes = len(self.text_box_client.toPlainText().encode("utf-8"))
        unselect_text_count = total_text_count - select_text_count
        unselect_text_bytes = total_text_bytes - select_text_bytes
        self.text_box_wordCountLabel.setText(
            f"{select_text_count} + {unselect_text_count} = {total_text_count} Words |  {select_text_bytes} + {unselect_text_bytes} = {total_text_bytes} Bytes"
        )
        if total_text_count > 10000:  # 字符数过多时自动清空
            self.text_box_client.clear()

    def edit_hot_en(self):
        os.startfile("hot-en.txt")

    def edit_hot_rule(self):
        os.startfile("hot-rule.txt")

    def edit_hot_zh(self):
        os.startfile("hot-zh.txt")

    def edit_keyword(self):
        os.startfile("keywords.txt")

    def explore_home_folder(self):
        current_directory = os.getcwd()
        os.startfile(current_directory)

    def vscode_home_folder(self):
        current_directory = os.getcwd()
        vscode_exe_path = Config.vscode_exe_path
        subprocess.Popen([vscode_exe_path, current_directory])

    def open_chatglm_website(self):
        os.system("start https://chatglm.cn/main/alltoolsdetail")

    def open_github_website(self):
        os.system("start https://github.com/H1DDENADM1N/CapsWriter-Offline")

    def closeEvent(self, event):
        # Minimize to system tray instead of closing the window when the user clicks the close button
        self.hide()  # Hide the window
        event.ignore()  # Ignore the close event

    def quit_app(self):
        # Terminate core_client.py process
        if hasattr(self, "core_client_process") and self.core_client_process:
            self.core_client_process.terminate()
            self.core_client_process.kill()

        # Hide the system tray icon
        self.tray_icon.setVisible(False)

        # Quit the application
        QApplication.quit()

        # TODO: Quit models The above method can not completely exit the model, rename pythonw.exe to pythonw_CapsWriter.exe and taskkill. It's working but not the best way.
        try:
            subprocess.Popen(
                "taskkill /IM start_client_gui_admin.exe /IM start_client_gui.exe /IM pythonw_CapsWriter_Client.exe /IM hint_while_recording.exe /F",
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=True,
                text=True,
            )
        except Exception:
            pass

    def on_tray_icon_activated(self, reason):
        # Called when the system tray icon is activated
        if reason == QSystemTrayIcon.DoubleClick:
            self.showNormal()  # Show the main window

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()  # Press ESC to hide main window

    def start_script(self):
        # Start core_client.py and redirect output to the client queue
        if Config.use_offline_translate_function:
            self.translate_and_replace_selected_text_offline_process = subprocess.Popen(
                [
                    ".\\runtime\\pythonw_CapsWriter_Client.exe",
                    ".\\util\\client_translate_and_replace_selected_text_offline.py",
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            threading.Thread(
                target=self.enqueue_output,
                args=(
                    self.translate_and_replace_selected_text_offline_process.stdout,
                    self.output_queue_client,
                ),
                daemon=True,
            ).start()

        if Config.use_online_translate_function:
            self.translate_and_replace_selected_text_online_process = subprocess.Popen(
                [
                    ".\\runtime\\pythonw_CapsWriter_Client.exe",
                    ".\\util\\client_translate_and_replace_selected_text_online.py",
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            threading.Thread(
                target=self.enqueue_output,
                args=(
                    self.translate_and_replace_selected_text_online_process.stdout,
                    self.output_queue_client,
                ),
                daemon=True,
            ).start()

        if Config.use_search_selected_text_with_everything_function:
            self.search_selected_text_with_everything = subprocess.Popen(
                [
                    ".\\runtime\\pythonw_CapsWriter_Client.exe",
                    ".\\util\\client_search_selected_text_with_everything.py",
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            threading.Thread(
                target=self.enqueue_output,
                args=(
                    self.search_selected_text_with_everything.stdout,
                    self.output_queue_client,
                ),
                daemon=True,
            ).start()

        self.core_client_process = subprocess.Popen(
            [".\\runtime\\pythonw_CapsWriter_Client.exe", "core_client.py"],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
        )
        threading.Thread(
            target=self.enqueue_output,
            args=(self.core_client_process.stdout, self.output_queue_client),
            daemon=True,
        ).start()

        # Update text box
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self.update_text_box)
        self.update_timer.start(100)

    def enqueue_output(self, out, queue):
        for line in iter(out.readline, ""):
            line = line.strip()
            queue.put(line)

    def update_text_box(self):
        # Update client text box
        while not self.output_queue_client.empty():
            try:
                line = self.output_queue_client.get()
                self.text_box_client.append(line)
            except Exception as e:
                self.text_box_client.append(e)
                break

    def checkWindowActive(self):
        # 检查窗口是否处于活跃状态
        if self.isActiveWindow():
            pass
        else:
            x, y, width, height, screenWidth, screenHeight = self.checkWindowInfo()
            if x == 0:  # 窗口非活跃状态，从左边弹出的，恢复继续停靠在左边
                self.berthToLeft(x, y, width, height, screenWidth, screenHeight)
            elif (
                x == screenWidth - width
            ):  # 窗口非活跃状态，从右边弹出的，恢复继续停靠在右边
                self.berthToRight(x, y, width, height, screenWidth, screenHeight)
            else:
                print("窗口无需恢复停靠")
                pass

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            delta = QPoint(event.globalPosition().toPoint() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPosition().toPoint()

    def enterEvent(self, event):
        super().enterEvent(event)
        for i in range(self.title_bar.count()):  # 鼠标进入时显示标题栏
            widget = self.title_bar.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(True)
        for i in range(self.layout2.count()):  # 鼠标进入时显示操作栏
            widget = self.layout2.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(True)
        x, y, width, height, screenWidth, screenHeight = self.checkWindowInfo()
        if self.isBerthLeft:  # 已停靠在左边
            self.move(0, y)  # 从左边弹出，31是标题栏高度
            self.isBerthLeft = False
        elif self.isBerthRight:  # 已停靠在右边
            self.move(screenWidth - width, y)  # 从右边弹出，31是标题栏高度
            self.isBerthRight = False
        else:
            # print("窗口未停靠")
            pass

    def leaveEvent(self, event):
        super().leaveEvent(event)
        for i in range(self.title_bar.count()):  # 鼠标离开时隐藏标题栏
            widget = self.title_bar.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(False)
        for i in range(self.layout2.count()):  # 鼠标离开时隐藏操作栏
            widget = self.layout2.itemAt(i).widget()
            if widget is not None:
                widget.setVisible(False)
        x, y, width, height, screenWidth, screenHeight = self.checkWindowInfo()
        # print(f"左右，高低，宽，高，屏宽，屏高: {(x, y, width, height, screenWidth, screenHeight)}")
        if self.isActiveWindow():  # 窗口活跃状态，用户点击了窗口，则不恢复继续停靠
            # print("窗口活跃状态")
            if x < 0 - width / 2:
                # print("活跃状态，但是窗口的一半已超出屏幕左边界，将窗口停靠在左边")
                self.berthToLeft(x, y, width, height, screenWidth, screenHeight)
            elif x > screenWidth - width / 2:
                # print("窗口活跃状态，但是窗口的一半已超出屏幕右边界，将窗口停靠在右边")
                self.berthToRight(x, y, width, height, screenWidth, screenHeight)
            else:
                # print("窗口活跃状态，无需停靠")
                pass
        else:  # 窗口非活跃状态，用户可能只是鼠标划过看一眼，失去焦点时恢复继续停靠
            # print("窗口不活跃状态")
            if x < 0 - width / 2:
                # print("窗口的一半已超出屏幕左边界")
                self.berthToLeft(x, y, width, height, screenWidth, screenHeight)
            elif x > screenWidth - width / 2:
                # print("窗口的一半已超出屏幕右边界")
                self.berthToRight(x, y, width, height, screenWidth, screenHeight)
            elif x == 0:  # 窗口非活跃状态，从左边弹出的，恢复继续停靠在左边
                self.berthToLeft(x, y, width, height, screenWidth, screenHeight)
            elif (
                x == screenWidth - width
            ):  # 窗口非活跃状态，从右边弹出的，恢复继续停靠在右边
                self.berthToRight(x, y, width, height, screenWidth, screenHeight)
            else:
                # print("窗口未超出屏幕边界")
                pass

    def berthToLeft(self, x, y, width, height, screenWidth, screenHeight):
        self.move(0 - width + self.edgeMargin, y)  # 停靠到左边，31是标题栏高度
        self.isBerthLeft = True

    def berthToRight(self, x, y, width, height, screenWidth, screenHeight):
        self.move(screenWidth - self.edgeMargin, y)  # 停靠到右边，31是标题栏高度
        self.isBerthRight = True

    def checkWindowInfo(self):
        geometry = self.geometry()
        x = geometry.x()
        y = geometry.y()
        width = geometry.width()
        height = geometry.height()
        primaryScreen = QApplication.instance().primaryScreen()
        screenRect = primaryScreen.geometry()
        screenWidth = screenRect.width()
        screenHeight = screenRect.height()
        return x, y, width, height, screenWidth, screenHeight

    def wheelEvent(self, event: QWheelEvent):
        # 设置初始缩放因子
        self.scale_factor = 1.0
        # 设置缩放因子的最小和最大值
        self.min_scale = 0.5
        self.max_scale = 2.0
        # 检测Ctrl键是否被按下
        if event.modifiers() == Qt.ControlModifier:
            # 计算缩放因子
            # print(event.angleDelta().y())
            if event.angleDelta().y() > 0:
                self.scale_factor *= 1.1  # 放大
            elif event.angleDelta().y() < 0:
                self.scale_factor *= 0.9  # 缩小
            # 限制缩放因子的范围
            self.scale_factor = max(
                self.min_scale, min(self.max_scale, self.scale_factor)
            )
            # 应用缩放因子到所有控件
            self.apply_scale_factor()
        else:
            super().wheelEvent(event)

    def apply_scale_factor(self):
        # 应用缩放因子
        for widget in [self.text_box_client]:
            # 检查字体大小是否已设置，如果没有设置，则使用一个默认值
            current_font = widget.font()
            if current_font.pointSizeF() < 9:
                current_font.setPointSizeF(9)  # 设置一个默认字体大小
            current_font.setPointSizeF(current_font.pointSizeF() * self.scale_factor)
            widget.setFont(current_font)


def start_client_gui():
    Print_Screen_Scale()
    if Config.only_run_once and check_process("pythonw_CapsWriter_Client.exe"):
        raise Exception(
            "已经有一个客户端在运行了！（用户配置了 只允许运行一次，禁止多开；而且检测到 pythonw_CapsWriter_Client.exe 进程已在运行。如果你确定需要启动多个客户端同时运行，请先修改 config.py  class ClientConfig:  Only_run_once = False 。）"
        )
    if (
        Config.hint_while_recording_at_edit_position_powered_by_ahk
        and not check_process("hint_while_recording.exe")
        and Path("hint_while_recording.exe").exists()
        # and Config.hold_mode
    ):
        subprocess.Popen(
            ["hint_while_recording.exe"], creationflags=subprocess.CREATE_NO_WINDOW
        )
    app = QApplication(sys.argv)
    if Config.hint_while_recording_at_cursor_position:
        tooltip = Hint_While_Recording_At_Cursor_Position()
        tooltip.show()
    apply_stylesheet(
        app, theme="dark_teal.xml", css_file="util\\client_gui_theme_custom.css"
    )
    global gui
    gui = GUI()
    if not Config.shrink_automatically_to_tray:
        gui.show()
    sys.exit(app.exec())


def Print_Screen_Scale():
    # 获取屏幕的宽度和高度
    hDC = win32gui.GetDC(0)
    screen_width = win32print.GetDeviceCaps(hDC, win32con.DESKTOPHORZRES)
    screen_height = win32print.GetDeviceCaps(hDC, win32con.DESKTOPVERTRES)
    print(f"屏幕尺寸: {screen_width}x{screen_height}")
    # 获取逻辑的宽度和高度
    logical_width = win32api.GetSystemMetrics(win32con.SM_CXVIRTUALSCREEN)
    logical_height = win32api.GetSystemMetrics(win32con.SM_CYVIRTUALSCREEN)
    print(f"逻辑尺寸: {logical_width}x{logical_height}")
    # 计算缩放比例
    global scale_x, scale_y
    scale_x = screen_width / logical_width
    scale_y = screen_height / logical_height
    print(f"屏幕缩放比例: {scale_x}, {scale_y}")


def read_file_list(file_list_path: Path):
    """读取文件列表文件，返回文件路径列表"""
    with open(file_list_path, "r", encoding="utf-8") as f:
        return [Path(line.strip()) for line in f if line.strip()]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="处理文件")
    parser.add_argument("files", nargs="*", type=Path, help="要处理的文件")
    parser.add_argument("--file-list", type=Path, help="包含文件列表的文本文件")
    args = parser.parse_args()

    if args.file_list:  # 如果传递了 --file-list 参数
        try:
            files = read_file_list(args.file_list)
        except Exception as e:
            print(f"Error reading file list: {e}")
            sys.exit(1)
    else:
        files = args.files  # 直接传递的文件列表

    if files:  # 如果有文件需要处理
        CapsWriter_path = Path(__file__).parent
        script_path = CapsWriter_path / "core_client.py"
        python_exe_path = CapsWriter_path / "runtime" / "python.exe"
        files_quoted = [str(file) for file in files]
        command = [str(python_exe_path), str(script_path)] + files_quoted
        try:
            subprocess.Popen(command, cwd=str(CapsWriter_path))
        except Exception as e:
            print(f"Error starting the process: {e}")
    else:
        # GUI
        start_client_gui()
