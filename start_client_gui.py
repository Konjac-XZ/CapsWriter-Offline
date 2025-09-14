import argparse
import os
import subprocess
import sys
import threading
import asyncio
from pathlib import Path
from queue import Queue
from dotenv import load_dotenv, find_dotenv, dotenv_values

# Project root is the directory containing this script; normalize CWD for reliability
ROOT: Path = Path(__file__).resolve().parent
try:
    os.chdir(str(ROOT))
except Exception:
    pass

# Always reload latest .env on startup; prefer files next to this script
try:
    env_file = ROOT / ".env"
    if env_file.exists():
        load_dotenv(str(env_file), override=True)
    env_local_file = ROOT / ".env.local"
    if env_local_file.exists():
        load_dotenv(str(env_local_file), override=True)
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
    QComboBox,
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

def _resolve_pythonw_client() -> str | None:
    """Return a usable Python interpreter for client child processes.

    Preference order:
    - runtime/pythonw_CapsWriter_Client.exe
    - runtime/pythonw.exe
    - runtime/python.exe
    - current sys.executable
    """
    candidates: list[Path] = [
        ROOT / "runtime" / "pythonw_CapsWriter_Client.exe",
        ROOT / "runtime" / "pythonw.exe",
        ROOT / "runtime" / "python.exe",
        Path(sys.executable) if sys.executable else None,  # type: ignore[arg-type]
    ]
    for p in candidates:
        if p and p.exists():
            return str(p)
    return None


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

        # Queue to store early log messages before UI is ready
        self.early_messages = []

        # Initialize transcription providers before UI setup
        self.initialize_transcription_providers()

        self.init_ui()
        self.output_queue_client = Queue()
        self.start_script()
        self.edgeMargin = 5  # 侧边停靠残余像素值
        self.isBerthLeft = False
        self.isBerthRight = False
        # Track last loaded env mapping to allow proper removals on reload
        try:
            self._last_env_mapping: dict[str, str] = self._read_env_files_dict()
        except Exception:
            self._last_env_mapping = {}

        # Display early messages now that UI is ready
        for message, color in self.early_messages:
            self.append_colored_line(message, color)
        self.early_messages = []

    def log_message(self, message: str, color: str = "#ffffff"):
        """Log a message - stores early messages in queue if UI not ready."""
        if hasattr(self, 'text_box_client'):
            self.append_colored_line(message, color)
        else:
            self.early_messages.append((message, color))

    def initialize_transcription_providers(self):
        """Initialize transcription provider configurations."""
        try:
            self.log_message("正在加载转录服务商配置...", "#00d4ff")
            from util.provider_config import provider_manager
            self.provider_manager = provider_manager
            self.provider_manager.load_providers()

            providers = self.provider_manager.list_providers()
            self.log_message(f"已加载 {len(providers)} 个转录服务商配置", "#00d4ff")
            for provider in providers:
                status = "启用" if provider['enabled'] else "禁用"
                self.log_message(f"  - {provider['name']} ({provider['type']}) [{status}]", "#888888")

            active = self.provider_manager.get_active_provider()
            if active:
                self.log_message(f"当前活动服务商: {active.name}", "#00ff00")
            else:
                self.log_message("未找到活动的转录服务商", "#ff8800")

        except ImportError as e:
            self.log_message(f"无法导入 provider_config: {e}", "#ff0000")
            self.provider_manager = None
        except Exception as e:
            self.log_message(f"初始化转录服务商时出错: {e}", "#ff0000")
            import traceback
            self.log_message(f"错误详情: {traceback.format_exc()}", "#ff0000")
            self.provider_manager = None

    def init_ui(self):
        self.setWindowTitle("CapsWriter-Offline-Client")
        try:
            self.setWindowIcon(QIcon(str(ROOT / "assets" / "client-icon.ico")))
        except Exception:
            pass
        self.setWindowOpacity(1.0)

        # Keep the main window always on top and hide it from the taskbar.
        # Use Qt.WindowStaysOnTopHint to keep above other windows and
        # Qt.Tool to prevent a taskbar entry on Windows while still allowing
        # the window to behave as a normal top-level window.
        try:
            flags = self.windowFlags() | Qt.WindowStaysOnTopHint
            self.setWindowFlags(flags)
        except Exception:
            pass

        # Use native system title bar; no custom frame
        self.create_text_box()
        self.create_provider_selector()
        self.create_systray_icon()

        # Layout
        self.layout = QVBoxLayout()
        self.layout.setSpacing(0)
        self.layout.setContentsMargins(3, 3, 3, 3)
        self.layout.addWidget(self.text_box_client)
        self.layout.addLayout(self.provider_layout)

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

    def create_provider_selector(self):
        """Create provider selection UI below the main text box."""
        self.provider_layout = QHBoxLayout()
        self.provider_layout.setSpacing(8)
        self.provider_layout.setContentsMargins(3, 3, 3, 3)

        # Provider label
        provider_label = QLabel("转录服务商:")
        provider_label.setMinimumWidth(70)
        self.provider_layout.addWidget(provider_label)

        # Provider dropdown
        self.provider_combo = QComboBox()
        self.provider_combo.setMinimumWidth(200)
        self.populate_provider_combo()
        self.provider_combo.currentTextChanged.connect(self.on_provider_changed)
        self.provider_layout.addWidget(self.provider_combo)

        # Test All button
        self.test_all_button = QPushButton("Test All")
        self.test_all_button.setMinimumWidth(80)
        self.test_all_button.setToolTip("测试所有转录服务商的可用性")
        self.test_all_button.clicked.connect(self.test_all_providers)
        self.provider_layout.addWidget(self.test_all_button)

        # Add spacer to push everything to the left
        self.provider_layout.addStretch()

    def populate_provider_combo(self):
        """Populate the provider combo box with available providers."""
        self.provider_combo.clear()

        if not self.provider_manager:
            self.provider_combo.addItem("环境配置模式 (.env)", None)
            return

        providers = self.provider_manager.list_providers()
        if not providers:
            self.provider_combo.addItem("未找到转录服务商配置", None)
            self.log_message("未找到任何转录服务商配置文件", "#ff8800")
            return

        active_provider = self.provider_manager.get_active_provider()
        active_index = 0

        for i, provider_info in enumerate(providers):
            display_name = f"{provider_info['name']} ({provider_info['type']})"
            self.provider_combo.addItem(display_name, provider_info['id'])

            if active_provider and provider_info['id'] == self.provider_manager.active_provider:
                active_index = i

        if providers:
            self.provider_combo.setCurrentIndex(active_index)
            self.log_message(f"转录服务商选择器已准备就绪，共 {len(providers)} 个选项", "#00d4ff")

    def on_provider_changed(self, display_name: str):
        """Handle provider selection change."""
        if not self.provider_manager:
            return

        current_data = self.provider_combo.currentData()
        if current_data is None:
            return

        provider_id = current_data
        if self.provider_manager.set_active_provider(provider_id):
            provider = self.provider_manager.get_provider(provider_id)
            if provider:
                self.append_colored_line(f"已切换至转录服务商: {provider.name}", "#00d4ff")
                # Restart workers to apply new provider settings
                self.restart_children_with_env()

    def test_all_providers(self):
        """Test all providers for availability."""
        if not self.provider_manager:
            self.log_message("无法测试：转录服务商管理器不可用", "#ff8800")
            return

        # Check if test audio file exists
        test_audio_path = ROOT / "AvailabilityTest.mp3"
        if not test_audio_path.exists():
            self.log_message("无法测试：未找到测试音频文件 AvailabilityTest.mp3", "#ff0000")
            return

        # Disable the test button during testing
        self.test_all_button.setEnabled(False)
        self.test_all_button.setText("测试中...")

        self.log_message("开始测试所有转录服务商的可用性...", "#00d4ff")

        # Run the test in a separate thread to avoid blocking the UI
        threading.Thread(
            target=self._run_availability_test,
            args=(test_audio_path,),
            daemon=True
        ).start()

    def _run_availability_test(self, test_audio_path: Path):
        """Run availability test in a separate thread."""
        try:
            # Import and run the test
            from util.provider_availability_test import run_availability_test

            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                results = loop.run_until_complete(run_availability_test(test_audio_path))

                # Format and display results
                from util.provider_availability_test import ProviderAvailabilityTester
                tester = ProviderAvailabilityTester(test_audio_path)
                formatted_results = tester.format_results(results)

                # Post results back to GUI thread
                self._post_test_results(formatted_results)

            finally:
                loop.close()

        except Exception as e:
            error_message = f"测试过程中发生错误: {str(e)}"
            self._post_test_results(error_message)

    def _post_test_results(self, results: str):
        """Post test results back to the GUI thread."""
        # Use QTimer.singleShot to safely update GUI from another thread
        from PySide6.QtCore import QTimer

        def update_gui():
            # Display results
            for line in results.split('\n'):
                if line.strip():
                    if "✅" in line:
                        self.log_message(line, "#00ff00")
                    elif "❌" in line:
                        self.log_message(line, "#ff0000")
                    elif line.startswith("==="):
                        self.log_message(line, "#00d4ff")
                    else:
                        self.log_message(line, "#ffffff")

            # Re-enable the test button
            self.test_all_button.setEnabled(True)
            self.test_all_button.setText("Test All")

        QTimer.singleShot(0, update_gui)

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
            """Normalize an env value for display.

            - None -> "(none)"
            - strip surrounding quotes and outer whitespace
            """
            if val is None:
                return "(none)"
            v = str(val).strip()
            if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                v = v[1:-1].strip()
            return v or "(none)"

        # Prefer values from .env (loaded earlier via dotenv). Fall back to config.py.
        transcribe_provider = _sanitize(os.environ.get("TRANSCRIBE_PROVIDER"))
        transcribe_prompt = _sanitize(os.environ.get("TRANSCRIBE_PROMPT"))
        transcribe_model = _sanitize(os.environ.get("TRANSCRIBE_MODEL"))
        transcribe_temperature = _sanitize(os.environ.get("TRANSCRIBE_TEMPERATURE"))

        # If the env explicitly disables model display, fall back to server config where appropriate
        if transcribe_model == "(none)":
            try:
                transcribe_model = ServerConfig.model
            except Exception:
                transcribe_model = "(unknown)"

        # Only expose OpenAI-specific settings when provider is openai
        transcribe_base_url: str | None = None
        if transcribe_provider == "openai":
            transcribe_base_url = _sanitize(os.environ.get("OPENAI_BASE_URL"))

        # Normalize multiline prompt to a single line for display
        if transcribe_prompt not in (None, "(none)"):
            transcribe_prompt = " ".join(line.strip() for line in transcribe_prompt.splitlines() if line.strip())

        # Output: provider always, OpenAI-only details only when applicable
        self.text_box_client.append(f"转录服务提供商: {transcribe_provider}")
        if transcribe_provider == "openai":
            self.text_box_client.append(f"转录基础 URL: {transcribe_base_url}")
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
        try:
            self.tray_icon.setIcon(QIcon(str(ROOT / "assets" / "client-icon.ico")))
        except Exception:
            pass

        edit_env_action = QAction("🛠️ Edit .env", self)
        reload_env_action = QAction("⚡ Apply .env (fast)", self)
        test_all_action = QAction("🧪 Test All Providers", self)
        explore_home_folder_action = QAction("📁 Open Home Folder With Explorer", self)
        vscode_home_folder_action = QAction("🤓 Open Home Folder With VSCode", self)

        show_action = QAction("🪟 Show", self)
        restart_client_action = QAction("🔄 Restart Client", self)
        quit_action = QAction("❌ Quit", self)

        edit_env_action.triggered.connect(self.edit_env)
        reload_env_action.triggered.connect(self.apply_env_fast)
        test_all_action.triggered.connect(self.run_test_all_providers)
        explore_home_folder_action.triggered.connect(self.explore_home_folder)
        vscode_home_folder_action.triggered.connect(self.vscode_home_folder)
        show_action.triggered.connect(self.showNormal)
        restart_client_action.triggered.connect(self.restart_client)
        quit_action.triggered.connect(self.quit_app)

        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        # Keep a persistent reference to avoid GC and enable warm-up
        self.tray_menu = QMenu()
        # Environment configuration shortcut replaces legacy hotword menu
        self.tray_menu.addAction(edit_env_action)
        self.tray_menu.addAction(reload_env_action)
        self.tray_menu.addAction(test_all_action)

        self.tray_menu.addSeparator()
        self.tray_menu.addAction(show_action)
        self.tray_menu.addAction(restart_client_action)
        self.tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.show()

        # Proactively warm up the tray menu to avoid first-use lag
        try:
            QTimer.singleShot(350, self._warm_up_tray_menu)
        except Exception:
            pass

    def run_test_all_providers(self):
        """Launch availability test script and stream its output to the GUI."""
        try:
            exe = _resolve_pythonw_client()
            if exe is None:
                self.text_box_client.append("无法启动测试：未找到可用的 Python 运行时。")
                return
            script = ROOT / "util" / "run_provider_availability_test.py"
            if not script.exists():
                self.text_box_client.append("找不到测试脚本：util/run_provider_availability_test.py")
                return
            self.append_colored_line("开始测试所有 OpenAI 类型服务商（每个最多 10 秒）…", QColor("#00d4ff"))
            p = subprocess.Popen(
                [exe, str(script)],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                cwd=str(ROOT),
                env=os.environ.copy(),
            )
            # Stream output
            threading.Thread(
                target=self.enqueue_output,
                args=(p.stdout, self.output_queue_client),
                daemon=True,
            ).start()
        except Exception as e:
            try:
                self.text_box_client.append(f"启动可用性测试失败: {e}")
            except Exception:
                pass

    def _warm_up_tray_menu(self):
        """Force-create and layout the tray menu to eliminate first-show stutter.

        We polish the menu, compute geometry, force a native handle, and briefly
        show it off-screen before hiding it again. This primes fonts/styles.
        """
        try:
            menu = getattr(self, "tray_menu", None)
            if not isinstance(menu, QMenu):
                return
            # Ensure style polish and layout
            try:
                menu.ensurePolished()
            except Exception:
                pass
            try:
                _ = menu.sizeHint()
                for act in menu.actions():
                    menu.actionGeometry(act)
            except Exception:
                pass
            # Force native handle creation
            try:
                _ = menu.winId()
            except Exception:
                pass
            # Briefly show off-screen to trigger any deferred init, then hide
            try:
                menu.move(-2000, -2000)
                menu.show()
                QApplication.processEvents()
                QTimer.singleShot(30, menu.hide)
            except Exception:
                pass
        except Exception:
            # Never let warm-up impact the app
            pass

    def restart_client(self):
        # Important: run the restart helper with the console Python (python.exe),
        # not pythonw_CapsWriter_Client.exe. Otherwise the helper would be killed
        # by its own taskkill (since it targets pythonw_CapsWriter_Client.exe).
        exe_console = str(ROOT / "runtime" / "python.exe")
        exe = exe_console if Path(exe_console).exists() else (sys.executable or exe_console)
        try:
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                [exe, str(ROOT / "util" / "client_restart.py")],
                creationflags=(subprocess.CREATE_NO_WINDOW | DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                cwd=str(ROOT),
            )
        except Exception as e:
            # Surface the error but keep GUI alive
            try:
                self.text_box_client.append(f"重启失败: {e}")
            except Exception:
                pass


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
        env_path = ROOT / ".env"
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
        try:
            os.startfile(str(ROOT))
        except Exception as e:
            try:
                self.text_box_client.append(f"打开目录失败: {e}")
            except Exception:
                pass

    def vscode_home_folder(self):
        current_directory = str(ROOT)
        vscode_exe_path = Config.vscode_exe_path
        try:
            subprocess.Popen([vscode_exe_path, current_directory], cwd=current_directory)
        except Exception as e:
            try:
                self.text_box_client.append(f"启动 VSCode 失败: {e}")
            except Exception:
                pass

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
        exe = _resolve_pythonw_client()
        if exe is None:
            try:
                self.text_box_client.append("未找到可用的 Python 运行时 (runtime/pythonw*_*.exe)。请检查 runtime 目录。")
            except Exception:
                pass
            return

        if Config.use_offline_translate_function:
            self.translate_and_replace_selected_text_offline_process = subprocess.Popen(
                [
                    exe,
                    str(ROOT / "util" / "client_translate_and_replace_selected_text_offline.py"),
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                cwd=str(ROOT),
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
                    exe,
                    str(ROOT / "util" / "client_translate_and_replace_selected_text_online.py"),
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                cwd=str(ROOT),
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
                    exe,
                    str(ROOT / "util" / "client_search_selected_text_with_everything.py"),
                ],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                cwd=str(ROOT),
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
            [exe, str(ROOT / "core_client.py")],
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            cwd=str(ROOT),
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

    # ============ Fast env reload and worker restart ============
    def _read_env_files_dict(self) -> dict[str, str]:
        """Read .env and .env.local into a merged dict without mutating os.environ.

        .env.local overrides .env when both define the same key.
        """
        base_path = ROOT / ".env"
        local_path = ROOT / ".env.local"
        data: dict[str, str] = {}
        try:
            if base_path.exists():
                data.update({k: str(v) for k, v in dotenv_values(str(base_path)).items() if v is not None})
        except Exception:
            pass
        try:
            if local_path.exists():
                # local overrides base
                data.update({k: str(v) for k, v in dotenv_values(str(local_path)).items() if v is not None})
        except Exception:
            pass
        return data

    def _apply_env_mapping_inplace(self, new_map: dict[str, str]) -> tuple[list[tuple[str, str | None, str]], list[str]]:
        """Update os.environ with new_map, remove keys that were previously loaded but now absent.

        Returns (changed, removed):
        - changed: list of (key, old_value, new_value)
        - removed: list of keys removed from os.environ
        """
        changed: list[tuple[str, str | None, str]] = []
        removed: list[str] = []
        prev = getattr(self, "_last_env_mapping", {})

        # Apply additions/updates
        for k, v in new_map.items():
            old = os.environ.get(k)
            if old != v:
                changed.append((k, old, v))
                os.environ[k] = v

        # Remove keys that were previously set from env files but are no longer present
        for k in prev.keys():
            if k not in new_map and k in os.environ:
                removed.append(k)
                try:
                    del os.environ[k]
                except Exception:
                    pass

        # Update snapshot
        self._last_env_mapping = dict(new_map)
        return changed, removed

    def _mask_value(self, key: str, value: str | None) -> str:
        """Mask sensitive values for display if key looks secret-like."""
        if value is None:
            return "(none)"
        k = key.lower()
        if any(s in k for s in ["key", "token", "secret", "pwd", "password", "api", "auth"]):
            if len(value) <= 6:
                return "*" * len(value)
            return value[:3] + "***" + value[-3:]
        return value

    def _stop_process(self, proc: subprocess.Popen | None, name: str) -> None:
        if not proc:
            return
        try:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=0.8)
                except Exception:
                    pass
                if proc.poll() is None:
                    proc.kill()
        except Exception:
            pass

    def _start_worker(self, script_rel_path: str, attr_name: str) -> None:
        exe = _resolve_pythonw_client()
        if exe is None:
            self.text_box_client.append("未找到可用的 Python 运行时。无法重启子进程。")
            return
        try:
            p = subprocess.Popen(
                [exe, str(ROOT / script_rel_path)],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                cwd=str(ROOT),
                env=os.environ.copy(),
            )
            setattr(self, attr_name, p)
            threading.Thread(
                target=self.enqueue_output,
                args=(p.stdout, self.output_queue_client),
                daemon=True,
            ).start()
        except Exception as e:
            self.text_box_client.append(f"启动子进程失败({script_rel_path}): {e}")

    def restart_children_with_env(self) -> None:
        """Restart only worker subprocesses to pick up new environment, keep GUI alive."""
        # Stop existing workers
        self._stop_process(getattr(self, "core_client_process", None), "core_client")
        if getattr(Config, "use_offline_translate_function", False):
            self._stop_process(getattr(self, "translate_and_replace_selected_text_offline_process", None), "offline_trans")
        if getattr(Config, "use_online_translate_function", False):
            self._stop_process(getattr(self, "translate_and_replace_selected_text_online_process", None), "online_trans")
        if getattr(Config, "use_search_selected_text_with_everything_function", False):
            self._stop_process(getattr(self, "search_selected_text_with_everything", None), "everything")

        # Start workers with updated env
        if getattr(Config, "use_offline_translate_function", False):
            self._start_worker("util/client_translate_and_replace_selected_text_offline.py", "translate_and_replace_selected_text_offline_process")
        if getattr(Config, "use_online_translate_function", False):
            self._start_worker("util/client_translate_and_replace_selected_text_online.py", "translate_and_replace_selected_text_online_process")
        if getattr(Config, "use_search_selected_text_with_everything_function", False):
            self._start_worker("util/client_search_selected_text_with_everything.py", "search_selected_text_with_everything")
        # Core client last
        self._start_worker("core_client.py", "core_client_process")

    def apply_env_fast(self):
        """Reload .env/.env.local and restart only background workers quickly."""
        try:
            new_map = self._read_env_files_dict()
            changed, removed = self._apply_env_mapping_inplace(new_map)
            ch_cnt = len(changed)
            rm_cnt = len(removed)
            # Brief summary with sensitive masking
            if ch_cnt or rm_cnt:
                self.append_colored_line(
                    f"已重新加载 .env（修改 {ch_cnt} 项, 移除 {rm_cnt} 项）。正在快速重启后台子进程…",
                    QColor("#00d4ff"),
                )
                preview_lines = []
                for k, old, new in changed[:5]:  # show at most 5 keys
                    preview_lines.append(f"  {k}: {self._mask_value(k, old)} -> {self._mask_value(k, new)}")
                for k in removed[:3]:
                    preview_lines.append(f"  {k}: removed")
                if preview_lines:
                    self.text_box_client.append("\n".join(preview_lines))
            else:
                self.text_box_client.append(".env 未检测到变化。仍将重启后台子进程以确保生效…")
        except Exception as e:
            self.text_box_client.append(f"读取 .env 失败: {e}")

        # Restart workers to apply env
        self.restart_children_with_env()
        # Refresh startup info display with latest env
        try:
            self.text_box_client.append("==== 环境已应用（快速） ====")
            self.show_startup_info()
        except Exception:
            pass


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
        widgets = [self.text_box_client]
        if hasattr(self, 'provider_combo'):
            widgets.append(self.provider_combo)
        if hasattr(self, 'test_all_button'):
            widgets.append(self.test_all_button)

        for widget in widgets:
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
        and (ROOT / "hint_while_recording.exe").exists()
        # and Config.hold_mode
    ):
        subprocess.Popen(
            [str(ROOT / "hint_while_recording.exe")],
            creationflags=subprocess.CREATE_NO_WINDOW,
            cwd=str(ROOT),
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
        app, theme="dark_teal.xml", css_file=str(ROOT / "util" / "client_gui_theme_custom.css")
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
