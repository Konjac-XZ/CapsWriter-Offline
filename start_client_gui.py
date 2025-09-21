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
# Intentionally defer theme import/application until after first paint for faster startup

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


# AHK hint tooltip removed for leaner startup

class GUI(QMainWindow):
    def __init__(self):
        super().__init__()

        # Queue to store early log messages before UI is ready
        self.early_messages = []
        # Ensure provider_manager attribute exists before UI uses it
        self.provider_manager = None

        self.init_ui()
        self.output_queue_client = Queue()
        self.edgeMargin = 5  # 侧边停靠残余像素值
        self.isBerthLeft = False
        self.isBerthRight = False

        # Display early messages now that UI is ready
        for message, color in self.early_messages:
            self.append_colored_line(message, color)
        self.early_messages = []

    def log_message(self, message: str, color: str = "#000000"):
        """Log a message - stores early messages in queue if UI not ready."""
        if hasattr(self, 'text_box_client'):
            self.append_colored_line(message, color)
        else:
            self.early_messages.append((message, color))

    def initialize_transcription_providers(self):
        """Initialize transcription provider configurations."""
        try:
            self.log_message("正在加载转录服务商配置...")
            from util.provider_config import provider_manager
            self.provider_manager = provider_manager
            self.provider_manager.load_providers()

            providers = self.provider_manager.list_providers()
            self.log_message(f"已加载 {len(providers)} 个转录服务商配置")
            for provider in providers:
                status = "启用" if provider['enabled'] else "禁用"
                self.log_message(f"  - {provider['name']} ({provider['type']}) [{status}]", "#888888")

            active = self.provider_manager.get_active_provider()
            if active:
                self.log_message(f"当前活动服务商: {active.name}", "#008000")
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
        # Defer heavy work to after first paint
        try:
            QTimer.singleShot(0, self._deferred_startup)
        except Exception:
            # Fallback if singleShot fails
            try:
                self._deferred_startup()
            except Exception:
                pass
        

    @staticmethod
    def _preferred_cn_font_family() -> str:
        """Return a CN-first font family available on this system.

        Prioritizes common Simplified Chinese UI fonts to avoid JP glyph fallbacks.
        """
        # Use a fixed font to avoid enumerating system fonts on startup
        return "Microsoft YaHei UI"

    def _deferred_startup(self):
        """Run expensive startup steps after the window is responsive."""
        # Load providers (I/O + YAML parse)
        try:
            self.initialize_transcription_providers()
        except Exception:
            pass
        # Refresh UI combos now that providers are available
        try:
            self.populate_provider_combo()
            self.populate_model_combo()
        except Exception:
            pass
        # Start background workers (core first, helpers staggered)
        try:
            self.start_script()
        except Exception:
            pass


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
        self.provider_layout = QVBoxLayout()
        self.provider_layout.setSpacing(8)
        self.provider_layout.setContentsMargins(3, 3, 3, 3)

        # Provider row
        provider_row = QHBoxLayout()
        provider_label = QLabel("转录服务商:")
        provider_label.setMinimumWidth(70)
        provider_row.addWidget(provider_label)

        self.provider_combo = QComboBox()
        self.provider_combo.setMinimumWidth(200)
        self.populate_provider_combo()
        self.provider_combo.currentTextChanged.connect(self.on_provider_changed)
        provider_row.addWidget(self.provider_combo)
        provider_row.addStretch()
        self.provider_layout.addLayout(provider_row)

        # Model row (only for OpenAI-type providers)
        self.model_row = QHBoxLayout()
        self.model_label = QLabel("　　　模型:")
        self.model_label.setMinimumWidth(40)
        self.model_combo = QComboBox()
        # Allow arbitrary model ids; users can type custom values
        self.model_combo.setEditable(True)
        self.model_combo.setMinimumWidth(180)
        self.model_combo.currentTextChanged.connect(self.on_model_changed)
        self.model_row.addWidget(self.model_label)
        self.model_row.addWidget(self.model_combo)
        self.model_row.addStretch()
        self.model_label.setVisible(False)
        self.model_combo.setVisible(False)
        self.provider_layout.addLayout(self.model_row)
        # Populate initial model list according to active provider
        self.populate_model_combo()

        # Test All button
        self.test_all_button = QPushButton("测试全部")
        self.test_all_button.setMinimumWidth(80)
        self.test_all_button.setToolTip("测试所有转录服务商的可用性")
        self.test_all_button.clicked.connect(self.test_all_providers)
        self.model_row.addWidget(self.test_all_button)

    def populate_provider_combo(self):
        """Populate the provider combo box with available providers."""
        # Avoid emitting currentTextChanged while we rebuild items to prevent
        # unintended provider switches on startup.
        self.provider_combo.blockSignals(True)
        try:
            self.provider_combo.clear()

            if not self.provider_manager:
                self.provider_combo.addItem("转录服务商配置系统未初始化", None)
                return

            providers = self.provider_manager.list_providers() or []
            if not providers:
                self.provider_combo.addItem("未找到转录服务商配置", None)
                self.log_message("未找到任何转录服务商配置文件")
                return

            # Normalize active provider id for robust matching (case-insensitive)
            try:
                active_id_raw = getattr(self.provider_manager, "active_provider", None)
                active_id_norm = (
                    str(active_id_raw).strip().lower() if active_id_raw is not None else None
                )
            except Exception:
                active_id_norm = None

            active_index: int | None = None

            for i, provider_info in enumerate(providers):
                display_name = f"{provider_info['name']} ({provider_info['type']})"
                pid = provider_info.get('id')
                self.provider_combo.addItem(display_name, pid)

                if active_id_norm is not None and pid is not None:
                    try:
                        if str(pid).strip().lower() == active_id_norm:
                            active_index = i
                    except Exception:
                        pass

            # Select the active provider if we found it; otherwise leave the first item selected
            if active_index is not None:
                # Only change if different to avoid needless churn
                if self.provider_combo.currentIndex() != active_index:
                    self.provider_combo.setCurrentIndex(active_index)

            self.log_message(
                f"转录服务商选择器已准备就绪，共 {len(providers)} 个选项"
            )
        finally:
            self.provider_combo.blockSignals(False)

    def on_provider_changed(self, display_name: str):
        """Handle provider selection change."""
        if not self.provider_manager:
            return

        current_data = self.provider_combo.currentData()
        if current_data is None:
            return

        # Skip if selection equals current active provider (avoid redundant switches on startup)
        try:
            active_id = getattr(self.provider_manager, "active_provider", None)
            if active_id is not None and str(current_data).strip().lower() == str(active_id).strip().lower():
                return
        except Exception:
            pass

        provider_id = current_data
        if self.provider_manager.set_active_provider(provider_id):
            provider = self.provider_manager.get_provider(provider_id)
            if provider:
                self.append_colored_line(f"已切换至转录服务商: {provider.name}")
                # Restart workers to apply new provider settings
                self.restart_children_with_env()
                # Refresh model selector visibility and values
                self.populate_model_combo()

    def _collect_known_openai_models(self) -> list[str]:
        """Collect a reasonable list of model options for OpenAI-compatible providers.

        - Gather models referenced in provider YAMLs
        - Include any current env setting
        - Add a small set of sensible defaults
        """
        models: list[str] = []
        try:
            if self.provider_manager:
                for p in self.provider_manager.providers.values():
                    if getattr(p, "type", "").lower() == "openai":
                        m = (p.settings or {}).get("model") if hasattr(p, "settings") else None
                        if isinstance(m, str) and m.strip():
                            models.append(m.strip())
        except Exception:
            pass
        # Add env value if present
        try:
            m_env = os.getenv("TRANSCRIBE_MODEL")
            if m_env and m_env.strip():
                models.append(m_env.strip())
        except Exception:
            pass
        # Sensible defaults
        models.extend([
            "gpt-4o-transcribe",
            "gpt-4o-mini-transcribe",
            "whisper-1",
        ])
        # De-duplicate while preserving order
        seen = set()
        uniq: list[str] = []
        for m in models:
            if m not in seen:
                uniq.append(m)
                seen.add(m)
        return uniq

    def populate_model_combo(self):
        """Update the model dropdown based on the currently selected provider.

        Visible only for OpenAI-type providers. Sets current value from provider.settings.model
        (or env TRANSCRIBE_MODEL) and offers a small curated list plus any discovered values.
        """
        # Default to hidden
        self.model_label.setVisible(False)
        self.model_combo.setVisible(False)

        if not self.provider_manager:
            return

        current_data = self.provider_combo.currentData()
        if current_data is None:
            return

        provider = self.provider_manager.get_provider(current_data)
        if not provider:
            return

        if getattr(provider, "type", "").lower() != "openai":
            # Non-OpenAI providers don't use this selection
            return

        # At this point, show controls
        self.model_label.setVisible(True)
        self.model_combo.setVisible(True)

        # Determine current model value
        current_model = None
        try:
            current_model = (provider.settings or {}).get("model")
        except Exception:
            current_model = None
        if not current_model:
            current_model = os.getenv("TRANSCRIBE_MODEL", "gpt-4o-transcribe")

        # Populate list
        options = self._collect_known_openai_models()
        self.model_combo.blockSignals(True)
        try:
            self.model_combo.clear()
            for opt in options:
                self.model_combo.addItem(opt)
            # Set current text, allowing custom entries
            self.model_combo.setEditText(str(current_model))
        finally:
            self.model_combo.blockSignals(False)

    def on_model_changed(self, model_name: str):
        """Handle model selection change for OpenAI providers: persist and restart workers."""
        if not self.provider_manager:
            return
        current_data = self.provider_combo.currentData()
        if current_data is None:
            return
        provider = self.provider_manager.get_provider(current_data)
        if not provider or getattr(provider, "type", "").lower() != "openai":
            return
        model = (model_name or "").strip()
        if not model:
            return
        # Persist to provider config (also updates env when active)
        ok = False
        try:
            ok = self.provider_manager.update_provider_model(current_data, model)
        except Exception:
            ok = False
        if ok:
            self.append_colored_line(f"已切换转录模型: {model}")
            # Restart workers to apply model change
            self.restart_children_with_env()
        else:
            self.append_colored_line("更新模型失败（请检查配置文件权限或格式）", "#ff5555")

    def test_all_providers(self):
        """Test all providers for availability using the same logic as tray menu."""
        self.run_test_all_providers()

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
                        self.log_message(line, "#008000")
                    elif "❌" in line:
                        self.log_message(line, "#ff0000")
                    elif line.startswith("==="):
                        self.log_message(line)
                    else:
                        self.log_message(line, "#000000")

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
        Always resets to default black color after appending to prevent color bleeding.
        """
        try:
            if isinstance(color, str):
                color = QColor(color)
            # Set the desired color
            self.text_box_client.setTextColor(color)
            self.text_box_client.append(text)
            # Always reset to default black color to prevent color bleeding
            self.text_box_client.setTextColor(QColor("#000000"))
        except Exception:
            # Fallback to plain append on any error, ensure color is reset
            try:
                self.text_box_client.setTextColor(QColor("#000000"))
                self.text_box_client.append(text)
            except Exception:
                pass

    def show_startup_info(self):
        """Show startup information using YAML-based provider configuration."""
        if not self.provider_manager:
            self.text_box_client.append("转录服务商配置系统未初始化")
            self.text_box_client.append("================")
            return

        active = self.provider_manager.get_active_provider()
        if active:
            self.text_box_client.append(f"转录服务提供商: {active.name} ({active.type})")

            # Show provider-specific settings
            if hasattr(active, 'settings') and active.settings:
                if active.type.lower() == "openai":
                    base_url = active.settings.get("base_url", "(none)")
                    model = active.settings.get("model", "(none)")
                    temperature = active.settings.get("temperature", "(none)")
                    self.text_box_client.append(f"转录基础 URL: {base_url}")
                    self.text_box_client.append(f"转录模型: {model}")
                    self.text_box_client.append(f"转录温度: {temperature}")

                    # Show prompt if available
                    prompt = active.settings.get("prompt")
                    if prompt:
                        # Normalize multiline prompt to a single line for display
                        prompt_normalized = " ".join(line.strip() for line in str(prompt).splitlines() if line.strip())
                        max_len = 1000
                        prompt_to_show = prompt_normalized if len(prompt_normalized) <= max_len else prompt_normalized[: max_len - 3] + "..."
                        self.text_box_client.append(f"转录提示: {prompt_to_show}")
                    else:
                        self.text_box_client.append("转录提示: (none)")
        else:
            self.text_box_client.append("转录服务提供商: 未配置")

        self.text_box_client.append("================")


    def create_systray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        try:
            self.tray_icon.setIcon(QIcon(str(ROOT / "assets" / "client-icon.ico")))
        except Exception:
            pass

        reload_providers_action = QAction("⚡ Reload Providers", self)
        test_all_action = QAction("🧪 Test All Providers", self)
        explore_home_folder_action = QAction("📁 Open Home Folder With Explorer", self)
        vscode_home_folder_action = QAction("🤓 Open Home Folder With VSCode", self)

        show_action = QAction("🪟 Show", self)
        restart_client_action = QAction("🔄 Restart Client", self)
        quit_action = QAction("❌ Quit", self)

        reload_providers_action.triggered.connect(self.reload_providers)
        test_all_action.triggered.connect(self.run_test_all_providers)
        explore_home_folder_action.triggered.connect(self.explore_home_folder)
        vscode_home_folder_action.triggered.connect(self.vscode_home_folder)
        show_action.triggered.connect(self.showNormal)
        restart_client_action.triggered.connect(self.restart_client)
        quit_action.triggered.connect(self.quit_app)

        self.tray_icon.activated.connect(self.on_tray_icon_activated)

        # Keep a persistent reference to avoid GC and enable warm-up
        self.tray_menu = QMenu()
        # Provider management shortcuts
        self.tray_menu.addAction(reload_providers_action)
        self.tray_menu.addAction(test_all_action)

        self.tray_menu.addSeparator()
        self.tray_menu.addAction(show_action)
        self.tray_menu.addAction(restart_client_action)
        self.tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.show()

        # Proactively warm up the tray menu to avoid first-use lag
        try:
            # Delay warm-up so first paint is smoother
            QTimer.singleShot(1200, self._warm_up_tray_menu)
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
            self.append_colored_line("开始测试所有 OpenAI 类型服务商（每个最多 10 秒）…", QColor("#000000"))
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

    def reload_providers(self):
        """Reload provider configurations and refresh the UI."""
        try:
            if self.provider_manager:
                self.provider_manager.load_providers()
                self.populate_provider_combo()
                self.populate_model_combo()
                self.log_message("已重新加载转录服务商配置")

                # Restart workers to apply any changes
                self.restart_children_with_env()
            else:
                self.log_message("转录服务商配置系统未初始化", "#ff8800")
        except Exception as e:
            self.log_message(f"重新加载转录服务商配置时出错: {e}", "#ff0000")

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
                "taskkill /IM start_client_gui_admin.exe /IM start_client_gui.exe /IM pythonw_CapsWriter_Client.exe /F",
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

        # Core first using common worker launcher
        try:
            self._start_worker("core_client.py", "core_client_process")
        except Exception:
            pass

        # Stagger optional helpers to reduce contention
        try:
            if getattr(Config, "use_offline_translate_function", False):
                QTimer.singleShot(300, lambda: self._start_worker(
                    "util/client_translate_and_replace_selected_text_offline.py",
                    "translate_and_replace_selected_text_offline_process"
                ))
            if getattr(Config, "use_online_translate_function", False):
                QTimer.singleShot(600, lambda: self._start_worker(
                    "util/client_translate_and_replace_selected_text_online.py",
                    "translate_and_replace_selected_text_online_process"
                ))
            if getattr(Config, "use_search_selected_text_with_everything_function", False):
                QTimer.singleShot(900, lambda: self._start_worker(
                    "util/client_search_selected_text_with_everything.py",
                    "search_selected_text_with_everything"
                ))
        except Exception:
            pass

        # Update text box
        try:
            self.update_timer = QTimer()
            self.update_timer.timeout.connect(self.update_text_box)
            self.update_timer.start(100)
        except Exception:
            pass

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

    # ============ Worker restart utilities ============

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
        if hasattr(self, 'model_combo'):
            widgets.append(self.model_combo)
        if hasattr(self, 'model_label'):
            widgets.append(self.model_label)

        for widget in widgets:
            # 检查字体大小是否已设置，如果没有设置，则使用一个默认值
            current_font = widget.font()
            if current_font.pointSizeF() < 9:
                current_font.setPointSizeF(9)  # 设置一个默认字体大小
            current_font.setPointSizeF(current_font.pointSizeF() * self.scale_factor)
            widget.setFont(current_font)


def _apply_theme_later(app: QApplication) -> None:
    """Apply qt_material theme after the first paint to improve perceived startup speed.

    Imports are done lazily; if anything fails, startup is not blocked.
    """
    try:
        from qt_material import apply_stylesheet  # local import to avoid import cost on cold start
    except Exception:
        return

    def do_apply():
        try:
            apply_stylesheet(
                app, theme="dark_teal.xml", css_file=str(ROOT / "util" / "client_gui_theme_custom.css")
            )
        except Exception:
            pass

    try:
        QTimer.singleShot(0, do_apply)
    except Exception:
        # Fallback: apply immediately if singleShot isn't available
        try:
            do_apply()
        except Exception:
            pass


def start_client_gui():
    if Config.only_run_once and check_process("pythonw_CapsWriter_Client.exe"):
        raise Exception(
            "已经有一个客户端在运行了！（用户配置了 只允许运行一次，禁止多开；而且检测到 pythonw_CapsWriter_Client.exe 进程已在运行。如果你确定需要启动多个客户端同时运行，请先修改 config.py  class ClientConfig:  Only_run_once = False 。）"
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
    # Defer theme application to improve first paint time
    _apply_theme_later(app)
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
