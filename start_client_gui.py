import argparse
import os
import subprocess
import sys
import threading
from pathlib import Path
from queue import Queue
from dotenv import load_dotenv, find_dotenv

# Always reload latest .env on startup (file values override inherited env)
try:
    # Load project .env if found
    _dotenv_path = find_dotenv(usecwd=True)
    if _dotenv_path:
        load_dotenv(_dotenv_path, override=True)
    # Load optional .env.local to override .env
    _dotenv_local = find_dotenv('.env.local', usecwd=True)
    if _dotenv_local:
        load_dotenv(_dotenv_local, override=True)
except Exception:
    # Don't block startup on dotenv issues
    pass

import win32api
import win32con
import win32gui
import win32print
from PySide6.QtCore import QPoint, Qt, QTimer, QLocale
from PySide6.QtGui import (
    QAction,
    QFont,
    QIcon,
    QWheelEvent,
    QFontDatabase,
    QTextOption,
    QShortcut,
    QKeySequence,
    QColor,
)
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
from util.config import ClientConfig as Config, ServerConfig, DeepLXConfig


class Hint_While_Recording_At_Cursor_Position(QLabel):
    def __init__(self):
        super().__init__()
    # Ensure MDL2 icon font for glyph rendering
        self.setFont(QFont("Segoe MDL2 Assets"))
        self.setWindowFlags(
            Qt.ToolTip | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        )
        self.setVisible(False)  # 初始时隐藏标签



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
        self.setWindowTitle("CapsWriter-Offline-Client")
        self.setWindowIcon(QIcon("assets/client-icon.ico"))
        self.setWindowOpacity(1.0)

        # Use native system title bar; no custom frame
        self.create_text_box()
        self.create_systray_icon()

        # Layout
        self.layout = QVBoxLayout()
        self.layout.setSpacing(0)
        self.layout.setContentsMargins(3, 3, 3, 3)
        self.layout.addWidget(self.text_box_client)

        # Central widget
        central_widget = QWidget()
        central_widget.setLayout(self.layout)
        self.setCentralWidget(central_widget)

        # Shortcut: Ctrl+L clears the text box
        clear_sc = QShortcut(QKeySequence("Ctrl+L"), self)
        clear_sc.activated.connect(self.clear_text_box)
        # Show startup info (colored)
        try:
            self.show_startup_info()
        except Exception:
            # Don't block UI if startup info fails
            pass
        self.text_box_client.append("准备就绪。")
        

    @staticmethod
    def _preferred_cn_font_family() -> str:
        """Return a CN-first font family available on this system.

        Prioritizes common Simplified Chinese UI fonts to avoid JP glyph fallbacks.
        """
        preferred = [
            "Microsoft YaHei UI",
            "Microsoft YaHei",
            "Noto Sans CJK SC",
            "Noto Sans SC",
            "Source Han Sans SC",
            "PingFang SC",
            "SimSun",
        ]
        try:
            families = set(QFontDatabase.families())
            for name in preferred:
                if name in families:
                    return name
        except Exception:
            pass
        return "Microsoft YaHei UI"


    # Removed custom title bar and its buttons; using native frame instead

    def create_text_box(self):
        self.text_box_client = QTextEdit()
        self.text_box_client.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.text_box_client.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Treat content strictly as plain text to avoid HTML rendering side-effects
        self.text_box_client.setAcceptRichText(False)
        # Make widget read-only to prevent user edits while still allowing programmatic updates
        self.text_box_client.setReadOnly(True)
        # Disable drag-and-drop to prevent dropping text into the widget
        self.text_box_client.setAcceptDrops(False)
        # Allow selection by mouse/keyboard but forbid editing
        self.text_box_client.setTextInteractionFlags(
            Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard
        )
        # Wrap at widget width and allow wrapping anywhere to avoid mid-glyph clipping for long CJK strings
        self.text_box_client.setLineWrapMode(QTextEdit.WidgetWidth)
        self.text_box_client.setWordWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        # Always follow the latest output (auto-scroll to the bottom on new text)
        try:
            self.text_box_client.textChanged.connect(self.scroll_to_bottom)
        except Exception:
            pass

    def scroll_to_bottom(self):
        """Pin the console view to the latest line after text changes."""
        try:
            sb = self.text_box_client.verticalScrollBar()
            if sb is not None:
                sb.setValue(sb.maximum())
            else:
                # Fallback: ensure cursor at end (rarely needed)
                cursor = self.text_box_client.textCursor()
                cursor.movePosition(cursor.End)
                self.text_box_client.setTextCursor(cursor)
                self.text_box_client.ensureCursorVisible()
        except Exception:
            pass

    def append_colored_line(self, text: str, color: QColor | str = "green"):
        """Append a single line to the client text box using the given color.

        Uses QTextEdit.setTextColor so rich text remains disabled but colored output is shown.
        """
        try:
            if isinstance(color, str):
                color = QColor(color)
            prev = self.text_box_client.textColor()
            self.text_box_client.setTextColor(color)
            self.text_box_client.append(text)
            # restore previous color
            self.text_box_client.setTextColor(prev)
        except Exception:
            # Fallback to plain append on any error
            try:
                self.text_box_client.append(text)
            except Exception:
                pass

    def show_startup_info(self):
        """Gather startup information from config and print it to the text box in green."""
        import os

        def _sanitize(val: str | None) -> str:
            if val is None:
                return "(none)"
            v = val
            # strip surrounding single or double quotes
            if (v.startswith('"') and v.endswith('"')) or (
                v.startswith("'") and v.endswith("'")
            ):
                v = v[1:-1]
            # trim whitespace
            v = v.strip()
            return v

        # Prefer values from .env (loaded earlier via dotenv). Fall back to config.py.
        transcribe_provider = _sanitize(os.environ.get("TRANSCRIBE_PROVIDER"))
        transcribe_prompt = _sanitize(os.environ.get("TRANSCRIBE_PROMPT"))
        transcribe_model = _sanitize(os.environ.get("TRANSCRIBE_MODEL"))
        transcribe_temperature = _sanitize(os.environ.get("TRANSCRIBE_TEMPERATURE"))

        if transcribe_model == "(none)":
            try:
                transcribe_model = ServerConfig.model
            except Exception:
                transcribe_model = "(unknown)"

        # Normalize multiline prompt to a single indented block for display
        if transcribe_prompt not in (None, "(none)"):
            transcribe_prompt = " ".join(line.strip() for line in transcribe_prompt.splitlines() if line.strip())

        self.text_box_client.append(f"转录服务提供商: {transcribe_provider}")
        self.text_box_client.append(f"转录模型: {transcribe_model}")
        self.text_box_client.append(f"转录温度: {transcribe_temperature}")
        # Print prompt on its own line; limit length to avoid overflowing the UI
        if transcribe_prompt and transcribe_prompt != "(none)":
            max_len = 1000
            prompt_to_show = transcribe_prompt if len(transcribe_prompt) <= max_len else transcribe_prompt[: max_len - 3] + "..."
            self.text_box_client.append(f"转录提示: {prompt_to_show}")
        else:
            self.text_box_client.append("转录提示: (none)")
        self.text_box_client.append("================")


    def create_systray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon("assets/client-icon.ico"))

        edit_env_action = QAction("🛠️ Edit .env", self)
        explore_home_folder_action = QAction("📁 Open Home Folder With Explorer", self)
        vscode_home_folder_action = QAction("🤓 Open Home Folder With VSCode", self)

        show_action = QAction("🪟 Show", self)
        restart_client_action = QAction("🔄 Restart Client", self)
        quit_action = QAction("❌ Quit", self)

        edit_env_action.triggered.connect(self.edit_env)
        explore_home_folder_action.triggered.connect(self.explore_home_folder)
        vscode_home_folder_action.triggered.connect(self.vscode_home_folder)
        show_action.triggered.connect(self.showNormal)
        restart_client_action.triggered.connect(self.restart_client)
        quit_action.triggered.connect(self.quit_app)

        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        tray_menu = QMenu()
        # Environment configuration shortcut replaces legacy hotword menu
        tray_menu.addAction(edit_env_action)

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


    def clear_text_box(self):
        # Clear the content of the client text box
        self.text_box_client.clear()


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
    # Removed stay-on-top toggle tied to custom title bar button

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

    def edit_env(self):
        """Open the project's .env file for editing; create it with a template if missing."""
        env_path = Path(".env")
        try:
            if not env_path.exists():
                template = (
                    "# CapsWriter-Offline environment variables\n"
                    "# Add key=value lines below. Examples:\n"
                    "# OPENAI_API_KEY=\n"
                    "# HTTP_PROXY=http://127.0.0.1:7890\n"
                    "# HTTPS_PROXY=http://127.0.0.1:7890\n"
                    "# See readme.md for details.\n"
                )
                env_path.write_text(template, encoding="utf-8")
            os.startfile(str(env_path))
        except Exception as e:
            # Non-fatal; surface the error in the log area if available
            try:
                self.text_box_client.append(f"Failed to open .env: {e}")
            except Exception:
                pass

    def explore_home_folder(self):
        current_directory = os.getcwd()
        os.startfile(current_directory)

    def vscode_home_folder(self):
        current_directory = os.getcwd()
        vscode_exe_path = Config.vscode_exe_path
        subprocess.Popen([vscode_exe_path, current_directory])

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
                # Support structured GUI messages emitted by util.gui_output.gui_print
                try:
                    if isinstance(line, str) and line.startswith("CW_GUI:"):
                        import json

                        payload = json.loads(line[len("CW_GUI:") :])
                        text = payload.get("text", "")
                        color = payload.get("color")
                        if color:
                            self.append_colored_line(text, color)
                        else:
                            self.text_box_client.append(text)
                        continue
                except Exception:
                    # Fall back to raw line on any parse error
                    pass

                self.text_box_client.append(line)
            except Exception as e:
                self.text_box_client.append(str(e))
                break


    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.LeftButton:
            delta = QPoint(event.globalPosition().toPoint() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPosition().toPoint()

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
    # Force CN locale to influence font fallback toward Simplified Chinese glyphs
    try:
        QLocale.setDefault(QLocale(QLocale.Chinese, QLocale.China))
    except Exception:
        pass
    # Set global font to Segoe UI with anti-aliasing and full hinting
    try:
        app_font = QFont(GUI._preferred_cn_font_family())
        # Prefer anti-aliased rendering
        if hasattr(QFont, "StyleStrategy") and hasattr(QFont.StyleStrategy, "PreferAntialias"):
            app_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        elif hasattr(QFont, "PreferAntialias"):
            app_font.setStyleStrategy(QFont.PreferAntialias)
        # Prefer full hinting if available
        if hasattr(QFont, "HintingPreference") and hasattr(QFont.HintingPreference, "PreferFullHinting"):
            app_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
        elif hasattr(QFont, "PreferFullHinting"):
            app_font.setHintingPreference(QFont.PreferFullHinting)
        app.setFont(app_font)
    except Exception as e:
        print(f"Error setting app font: {e}")
        pass
    if Config.hint_while_recording_at_cursor_position:
        tooltip = Hint_While_Recording_At_Cursor_Position()
        # Ensure icon glyph renders using MDL2 font regardless of global font
        try:
            tooltip.setFont(QFont("Segoe MDL2 Assets"))
        except Exception:
            pass
        tooltip.show()
    apply_stylesheet(
        app, theme="dark_teal.xml", css_file="util\\client_gui_theme_custom.css"
    )
    # Print screen info after Qt app is initialized (accurate in multi-monitor setups)
    try:
        Print_Screen_Scale()
    except Exception as e:
        # Don't block startup if printing screen info fails
        print(f"Print_Screen_Scale error: {e}")
    global gui
    gui = GUI()
    if not Config.shrink_automatically_to_tray:
        gui.show()
    sys.exit(app.exec())


def Print_Screen_Scale():
    """打印多显示器场景下更准确的屏幕信息。

    - 逻辑尺寸: 使用 Qt 的 virtualGeometry 获取整个虚拟桌面的逻辑像素尺寸。
    - 缩放比例: 使用主屏的逻辑 DPI 推导缩放比例（dpi/96）。
    注：当多显示器缩放不同步时，仅报告主屏缩放比例，避免将“总物理像素/总逻辑像素”误判为缩放。
    """
    from PySide6.QtGui import QGuiApplication

    screen = QGuiApplication.primaryScreen()
    if screen is None:
        raise RuntimeError("No primary screen available")

    # 虚拟桌面的逻辑像素尺寸（包含所有扩展显示器）
    vrect = screen.virtualGeometry()
    logical_width = int(vrect.width())
    logical_height = int(vrect.height())
    print(f"逻辑尺寸(虚拟桌面): {logical_width}x{logical_height}")

    # 主屏缩放比例，基于逻辑 DPI（96 DPI 视为 100%）
    dpi_x = float(getattr(screen, "logicalDotsPerInchX", lambda: screen.logicalDotsPerInch())())
    dpi_y = float(getattr(screen, "logicalDotsPerInchY", lambda: screen.logicalDotsPerInch())())

    global scale_x, scale_y
    scale_x = dpi_x / 96.0 if dpi_x else 1.0
    scale_y = dpi_y / 96.0 if dpi_y else 1.0
    print(f"主屏缩放比例: {scale_x:.2f}, {scale_y:.2f}")

    # 估算物理像素尺寸（按主屏缩放比例，仅作参考）
    est_physical_w = int(round(logical_width * scale_x))
    est_physical_h = int(round(logical_height * scale_y))
    print(f"估算虚拟桌面物理像素(按主屏缩放): {est_physical_w}x{est_physical_h}")

    # Fallback: 若需要原生 Win32 值，可在调试时取消注释
    # hDC = win32gui.GetDC(0)
    # try:
    #     desktop_w = win32print.GetDeviceCaps(hDC, win32con.DESKTOPHORZRES)
    #     desktop_h = win32print.GetDeviceCaps(hDC, win32con.DESKTOPVERTRES)
    #     print(f"原生桌面像素(供参考): {desktop_w}x{desktop_h}")
    # finally:
    #     win32gui.ReleaseDC(0, hDC)


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
