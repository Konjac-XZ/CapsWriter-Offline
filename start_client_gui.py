import argparse
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Sequence, cast

import yaml

from PySide6.QtCore import QPoint, QSize, Qt, QTimer
from PySide6.QtGui import (
    QIcon,
    QStandardItemModel,
    QWheelEvent,
    QTextDocument,
    QTextOption,
    QTextCursor,
    QTextCharFormat,
    QShortcut,
    QKeySequence,
    QColor,
    QGuiApplication,
)
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QStyle,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)
# Intentionally defer theme import/application until after first paint for faster startup

from src.gui.runtime import (
    ROOT,
    client_icon_path,
    core_client_script_path,
    ensure_project_cwd,
    load_startup_env,
    read_file_list,
    resolve_console_python,
    resolve_pythonw_client,
    restart_script_path,
)

ensure_project_cwd()
load_startup_env()

from src.infra.config import config as Config
from src.infra.runtime_logging import configure_runtime_logging, record_console_message
from src.infra.daily_input_stats import get_today_input_count
from src.audio.control_requests import (
    write_abandon_request,
    write_clear_history_request,
)
from src.audio.retry_cache import (
    has_retry_audio,
    latest_audio_path_for_mime,
    write_retry_request,
)
from src.gui.app_startup import (
    apply_theme_later,
    configure_app_locale_and_font,
    print_screen_scale,
)
from src.gui.listening_overlay import StatusOverlayController
from src.gui.lexicon_editor_client import LexiconEditorProcessClient
from src.gui.prompt_editor import PromptEditDialog


from src.gui.startup_profiler import StartupProfileOptions, StartupProfiler
from src.gui.tray_process_client import TrayProcessClient
from src.gui.worker_output_router import WorkerOutputRouter
from src.polish.llm_polish import (
    get_polish_prompt_text,
    reload_polish_config,
    update_polish_prompt_text,
)
from src.system.process_cleanup import (
    terminate_executable_processes,
    terminate_python_script_basename_processes,
    terminate_python_script_processes,
)
from src.system.startup_replacement import (
    prepare_replacement_startup,
    release_startup_slot,
)

configure_runtime_logging("client_gui")


# AHK hint tooltip removed for leaner startup


GUI_COLOR_ALIASES = {
    "black": "#000000",
    "bright_black": "#666666",
    "dim": "#888888",
    "red": "#cc4444",
    "bright_red": "#ff5555",
    "yellow": "#ff8800",
    "bright_yellow": "#ff8800",
    "orange": "#ff8800",
    "green": "#008000",
    "bright_green": "#008000",
    "cyan": "#00a6c8",
    "bright_cyan": "#00d4ff",
    "blue": "#0066cc",
    "bright_blue": "#3388ff",
    "magenta": "#aa44aa",
    "bright_magenta": "#cc55cc",
    "white": "#000000",
    "bright_white": "#000000",
}


class AdaptivePopupComboBox(QComboBox):
    """Fit as many popup rows as possible into the space below the control."""

    _POPUP_MARGIN_PX = 6

    def _popup_row_height(self, index: int) -> int:
        row_height = self.view().sizeHintForRow(index)
        if row_height <= 0:
            row_height = self.sizeHint().height()
        return max(1, row_height)

    def _fit_popup_to_available_height(self, available_height: int) -> int:
        item_count = self.count()
        if item_count <= 0:
            self.setMaxVisibleItems(1)
            return 0

        view = self.view()
        chrome_height = max(2, view.frameWidth() * 2 + 2)
        row_budget = max(1, available_height - chrome_height)
        used_height = 0
        visible_items = 0
        total_row_height = 0
        for index in range(item_count):
            row_height = self._popup_row_height(index)
            total_row_height += row_height
            if visible_items == 0 or used_height + row_height <= row_budget:
                used_height += row_height
                visible_items += 1
            else:
                break

        visible_items = min(item_count, max(1, visible_items))
        self.setMaxVisibleItems(visible_items)
        view.setMaximumHeight(
            max(1, min(total_row_height + chrome_height, available_height))
        )
        return visible_items

    def showPopup(self) -> None:
        popup_origin = self.mapToGlobal(QPoint(0, self.height()))
        screen = QGuiApplication.screenAt(popup_origin) or self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            available_below = (
                available.bottom() - popup_origin.y() + 1 - self._POPUP_MARGIN_PX
            )
            self._fit_popup_to_available_height(max(1, available_below))
        else:
            self.setMaxVisibleItems(max(1, self.count()))
        super().showPopup()


GUI_TIMING_SLOW_MS = 100.0
GUI_TIMER_GAP_MS = 500.0
GUI_WATCHDOG_GAP_MS = 700.0
GUI_LOG_MAX_BLOCKS = 500


def _elapsed_ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000.0


def _configure_log_document_retention(
    document: QTextDocument,
    max_blocks: int = GUI_LOG_MAX_BLOCKS,
) -> None:
    """Keep recent GUI logs bounded without rebuilding the document."""
    document.setMaximumBlockCount(max(1, int(max_blocks)))


def _append_log_document_entries(
    document: QTextDocument,
    entries: list[tuple[str, QColor]],
) -> None:
    """Append colored plain-text entries as one document edit transaction."""
    if not entries:
        return
    cursor = QTextCursor(document)
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.beginEditBlock()
    try:
        for text, color in entries:
            if not document.isEmpty():
                cursor.insertBlock()
            fmt = QTextCharFormat()
            fmt.setForeground(color)
            cursor.insertText(str(text).replace("\n", "\u2029"), fmt)
    finally:
        cursor.endEditBlock()


def _resolve_ffplay_exe() -> str | None:
    if ffplay := shutil.which("ffplay"):
        return ffplay

    for candidate in (ROOT / "ffplay.exe", ROOT / "bin" / "ffplay.exe"):
        if candidate.exists():
            return str(candidate)

    return None


class GUI(QMainWindow):
    def __init__(self):
        super().__init__()

        # Queue to store early log messages before UI is ready
        self.early_messages: list[tuple[str, str]] = []
        # Ensure provider_manager attribute exists before UI uses it
        self.provider_manager: Any | None = None
        self._syncing_context_toggle_states = False
        self._last_worker_timer_tick = 0.0
        self._last_gui_heartbeat = time.monotonic()
        self._gui_thread_id = threading.get_ident()
        self._worker_timer_lag_reports = 0
        self._gui_timing_reports = 0
        self._suppress_scroll_timing = False
        self._suppress_append_timing = False
        self._watchdog_stop = threading.Event()
        self._watchdog_reports = 0
        self._worker_restart_lock = threading.Lock()
        self._worker_restart_running = False
        self._worker_restart_pending = False
        self._latest_wav_player: subprocess.Popen[bytes] | None = None
        self.core_client_process: subprocess.Popen[str] | None = None
        self.text_box_wordCountLabel: QLabel | None = None
        self.old_pos = QPoint()
        lexicon_python = resolve_pythonw_client() or sys.executable
        self._lexicon_editor_client = LexiconEditorProcessClient(ROOT, lexicon_python)
        self._lexicon_loading_dialog: QProgressDialog | None = None
        self._tray_process_client = TrayProcessClient(ROOT, lexicon_python)
        self._tray_event_timer = QTimer(self)
        self._tray_event_timer.timeout.connect(self._poll_tray_process_event)
        self._tray_event_timer.start(100)
        self._lexicon_event_timer = QTimer(self)
        self._lexicon_event_timer.timeout.connect(self._poll_lexicon_editor_event)
        self._lexicon_event_timer.start(250)

        self.init_ui()
        self.output_router = WorkerOutputRouter()
        self.output_router.overlay_event.connect(
            self._handle_status_overlay_event,
            Qt.ConnectionType.QueuedConnection,
        )
        self.output_router.context_event.connect(
            self._handle_context_toggle_event,
            Qt.ConnectionType.QueuedConnection,
        )
        self.status_overlay = StatusOverlayController()
        self.status_overlay.set_abandon_callback(self.abandon_current_task)
        self.status_overlay.set_timing_callback(
            lambda message: self._log_gui_timing(f"overlay {message}")
        )
        self.edgeMargin = 5  # 侧边停靠残余像素值
        self.isBerthLeft = False
        self.isBerthRight = False
        threading.Thread(target=self._gui_watchdog_loop, daemon=True).start()

        # Display early messages now that UI is ready
        if self.early_messages:
            self._append_colored_entries(
                self.early_messages,
                source=f"early lines={len(self.early_messages)}",
            )
        self.early_messages.clear()

    def log_message(self, message: str, color: str = "#000000"):
        """Log a message - stores early messages in queue if UI not ready."""
        record_console_message(message, style=color)
        if hasattr(self, "text_box_client"):
            self.append_colored_line(message, color)
        else:
            self.early_messages.append((message, color))

    def initialize_transcription_providers(self):
        """Initialize transcription provider configurations."""
        try:
            self.log_message("正在加载转录服务商配置...")
            from src.provider.provider_config import provider_manager

            self.provider_manager = provider_manager
            self.provider_manager.load_providers()

            providers = self.provider_manager.list_providers()
            models = self.provider_manager.list_models()
            self.log_message(
                f"已加载 {len(providers)} 个转录服务商、{len(models)} 个转录模型"
            )
            for provider in providers:
                status = "启用" if provider["enabled"] else "禁用"
                self.log_message(
                    f"  - {provider['name']} ({provider['type']}) [{status}]", "#888888"
                )

            active = self.provider_manager.get_active_model()
            if active:
                self.log_message(
                    f"当前转录模型: {active.model_name} · {active.provider_name}",
                    "#008000",
                )
            else:
                self.log_message("未找到可用的转录模型", "#ff8800")

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
            self.setWindowIcon(QIcon(str(client_icon_path())))
        except Exception:
            pass
        self.setWindowOpacity(1.0)

        # Use native system title bar; no custom frame
        self.create_text_box()
        self.create_provider_selector()
        self.create_systray_icon()

        # Layout
        self.main_layout = QVBoxLayout()
        self.main_layout.setSpacing(0)
        self.main_layout.setContentsMargins(3, 3, 3, 3)
        self.main_layout.addWidget(self.text_box_client, 1)
        self.create_daily_input_count_label()
        self.main_layout.addWidget(self.daily_input_count_label)
        self.main_layout.addLayout(self.provider_layout)

        # Central widget
        central_widget = QWidget()
        central_widget.setLayout(self.main_layout)
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
        self.append_plain_line("界面已打开，正在启动监听进程...")
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
            self.populate_model_combo()
            self.sync_asr_realtime_control()
        except Exception:
            pass
        # Start background workers (core first, helpers staggered)
        try:
            self.start_script()
        except Exception:
            pass

    # Removed custom title bar and its buttons; using native frame instead

    def create_text_box(self):
        self.text_box_client = QPlainTextEdit()
        _configure_log_document_retention(self.text_box_client.document())
        self.text_box_client.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.text_box_client.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        # QPlainTextEdit is intrinsically plain text and avoids rich-document
        # parsing/layout work for the log console.
        self.text_box_client.setUndoRedoEnabled(False)
        # Make widget read-only to prevent user edits while still allowing programmatic updates
        self.text_box_client.setReadOnly(True)
        # Disable drag-and-drop to prevent dropping text into the widget
        self.text_box_client.setAcceptDrops(False)
        # Allow selection by mouse/keyboard but forbid editing
        self.text_box_client.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        # Wrap at widget width and allow wrapping anywhere to avoid mid-glyph clipping for long CJK strings
        self.text_box_client.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.text_box_client.setWordWrapMode(
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        )

    def create_daily_input_count_label(self) -> None:
        """Create the compact daily character counter below the output pane."""
        self.daily_input_count_label = QLabel()
        self.daily_input_count_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.daily_input_count_label.setStyleSheet("color: #666666; padding: 2px 4px;")
        self._refresh_daily_input_count()
        self.daily_input_count_timer = QTimer(self)
        self.daily_input_count_timer.timeout.connect(self._refresh_daily_input_count)
        self.daily_input_count_timer.start(1000)

    def _refresh_daily_input_count(self) -> None:
        try:
            count = get_today_input_count()
        except Exception:
            count = 0
        self.daily_input_count_label.setText(f"今日已输入 {count} 字")

    def _configure_collapsible_combo(
        self, combo: QComboBox, *, editable: bool = False
    ) -> None:
        """Apply a unified style and sizing policy to combo boxes."""
        combo.setEditable(editable)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setMinimumWidth(0)
        combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        combo.setMinimumContentsLength(0)
        combo.setStyleSheet("QComboBox { min-width: 0px; }")

    def _build_combo_field(
        self, label_text: str, combo: QComboBox
    ) -> tuple[QWidget, QLabel]:
        """Wrap a label-combo pair so the group collapses gracefully."""
        container = QWidget()
        layout = QHBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        label = QLabel(label_text)
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        layout.addWidget(label)
        layout.addWidget(combo)

        container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        return container, label

    @staticmethod
    def _polish_config_path() -> Path:
        return ROOT / "config" / "polish" / "polish.yaml"

    @staticmethod
    def _vision_config_path() -> Path:
        return ROOT / "config" / "polish" / "vision.yaml"

    def _create_context_toggle(self, text: str, tooltip: str) -> QCheckBox:
        checkbox = QCheckBox(text)
        checkbox.setToolTip(tooltip)
        checkbox.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        return checkbox

    def _update_yaml_bool(
        self, path: Path, key_path: tuple[str, ...], value: bool
    ) -> bool:
        try:
            original = path.read_text(encoding="utf-8")
        except Exception:
            return False

        replacement = "true" if value else "false"
        lines = original.splitlines(keepends=True)

        if len(key_path) == 1:
            key = key_path[0]
            pattern = re.compile(
                rf"^(?P<prefix>{re.escape(key)}\s*:\s*)(?P<value>true|false)(?P<suffix>\s*(#.*)?)$",
                re.IGNORECASE,
            )
            for idx, line in enumerate(lines):
                stripped = line.rstrip("\r\n")
                match = pattern.match(stripped)
                if not match:
                    continue
                newline = line[len(stripped) :]
                lines[idx] = (
                    f"{match.group('prefix')}{replacement}{match.group('suffix')}{newline}"
                )
                try:
                    path.write_text("".join(lines), encoding="utf-8")
                except Exception:
                    return False
                return True
            return False

        if len(key_path) == 2:
            section, key = key_path
            section_pattern = re.compile(rf"^{re.escape(section)}\s*:\s*$")
            value_pattern = re.compile(
                rf"^(?P<indent>\s+)(?P<prefix>{re.escape(key)}\s*:\s*)(?P<value>true|false)(?P<suffix>\s*(#.*)?)$",
                re.IGNORECASE,
            )
            in_section = False
            for idx, line in enumerate(lines):
                stripped = line.rstrip("\r\n")
                if not in_section:
                    if section_pattern.match(stripped):
                        in_section = True
                    continue

                if stripped and not stripped.startswith((" ", "\t", "#")):
                    break

                match = value_pattern.match(stripped)
                if not match:
                    continue

                newline = line[len(stripped) :]
                lines[idx] = (
                    f"{match.group('indent')}{match.group('prefix')}{replacement}"
                    f"{match.group('suffix')}{newline}"
                )
                try:
                    path.write_text("".join(lines), encoding="utf-8")
                except Exception:
                    return False
                return True
            return False

        return False

    def _load_bool_from_yaml(
        self, path: Path, key_path: tuple[str, ...], default: bool
    ) -> bool:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            return default

        current = data
        try:
            for key in key_path:
                current = current[key]
        except Exception:
            return default
        return bool(current)

    def _sync_context_toggle_states(self) -> None:
        self._syncing_context_toggle_states = True
        try:
            self.append_history_checkbox.setChecked(
                self._load_bool_from_yaml(
                    self._polish_config_path(), ("history", "enabled"), True
                )
            )
            self.append_textbox_checkbox.setChecked(
                self._load_bool_from_yaml(
                    self._polish_config_path(), ("textbox_context", "enabled"), True
                )
            )
            self.append_vision_checkbox.setChecked(
                self._load_bool_from_yaml(
                    self._vision_config_path(), ("enabled",), False
                )
            )
        finally:
            self._syncing_context_toggle_states = False

    def _apply_context_toggle_change(
        self,
        *,
        checked: bool,
        path: Path,
        key_path: tuple[str, ...],
        label: str,
        restart_workers: bool = False,
    ) -> None:
        if self._syncing_context_toggle_states:
            return

        if not self._update_yaml_bool(path, key_path, checked):
            self.append_colored_line(
                f"保存{label}开关失败（请检查配置文件权限或格式）", "#ff5555"
            )
            self._sync_context_toggle_states()
            return

        if path == self._polish_config_path():
            try:
                reload_polish_config()
            except Exception:
                pass

        state_text = "启用" if checked else "禁用"
        self.append_colored_line(f"已{state_text}{label}。", "#888888")

        if restart_workers:
            self.append_colored_line(
                "正在重启录音进程以应用视觉上下文设置。", "#888888"
            )
            self.restart_children_with_env()

    def on_append_history_toggled(self, checked: bool) -> None:
        self._apply_context_toggle_change(
            checked=checked,
            path=self._polish_config_path(),
            key_path=("history", "enabled"),
            label="附加最近上屏内容",
        )

    def on_append_textbox_toggled(self, checked: bool) -> None:
        self._apply_context_toggle_change(
            checked=checked,
            path=self._polish_config_path(),
            key_path=("textbox_context", "enabled"),
            label="附加文本框上下文",
        )

    def on_append_vision_toggled(self, checked: bool) -> None:
        self._apply_context_toggle_change(
            checked=checked,
            path=self._vision_config_path(),
            key_path=("enabled",),
            label="附加视觉上下文",
            restart_workers=True,
        )

    def create_provider_selector(self):
        """Create provider selection UI below the main text box."""
        self.provider_layout = QVBoxLayout()
        self.provider_layout.setSpacing(6)
        self.provider_layout.setContentsMargins(3, 3, 3, 3)

        provider_row = QHBoxLayout()
        provider_row.setSpacing(6)
        provider_row.setContentsMargins(0, 0, 0, 0)

        self.model_combo = AdaptivePopupComboBox()
        self._configure_collapsible_combo(self.model_combo)
        provider_field, self.model_label = self._build_combo_field(
            "转录模型:", self.model_combo
        )
        self.populate_model_combo()
        self.model_combo.currentIndexChanged.connect(self.on_model_changed)
        provider_row.addWidget(provider_field, 1)

        self.modify_prompt_button = QPushButton("编辑 ASR 提示词")
        self.modify_prompt_button.setToolTip("编辑当前转录服务商的自定义提示词")
        self.modify_prompt_button.clicked.connect(self.show_modify_prompt_dialog)
        self.modify_prompt_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        provider_row.addWidget(self.modify_prompt_button)

        prompt_action_row = QHBoxLayout()
        prompt_action_row.setSpacing(6)
        prompt_action_row.setContentsMargins(0, 0, 0, 0)

        self.asr_realtime_checkbox = QCheckBox("流式音频")
        self.asr_realtime_checkbox.setToolTip(
            "支持的语音识别服务会在录音时实时发送音频；关闭后改为录音结束后上传文件"
        )
        self.asr_realtime_checkbox.setSizePolicy(
            QSizePolicy.Policy.Fixed,
            QSizePolicy.Policy.Fixed,
        )
        self.asr_realtime_checkbox.toggled.connect(self.on_asr_realtime_toggled)
        prompt_action_row.addWidget(self.asr_realtime_checkbox)
        prompt_action_row.addStretch()

        self.edit_polish_prompt_button = QPushButton("编辑 LLM 提示词")
        self.edit_polish_prompt_button.setToolTip(
            "编辑 config/polish/polish.yaml 中的 LLM 润色提示词"
        )
        self.edit_polish_prompt_button.clicked.connect(
            self.show_edit_polish_prompt_dialog
        )
        self.edit_polish_prompt_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        prompt_action_row.addWidget(self.edit_polish_prompt_button)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        action_row.setContentsMargins(0, 0, 0, 0)

        self.clear_history_button = QPushButton("清除最近上屏")
        self.clear_history_button.setToolTip("暂时清除 LLM 润色使用的最近上屏消息记录")
        self.clear_history_button.clicked.connect(self.clear_recent_output_history)
        self.clear_history_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        action_row.addWidget(self.clear_history_button)

        self.retry_latest_button = QPushButton("重试最近请求")
        self.retry_latest_button.setToolTip(
            "重新发送最近一次录音缓存，并将结果照常上屏"
        )
        self.retry_latest_button.clicked.connect(self.retry_latest_request)
        self.retry_latest_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        action_row.addWidget(self.retry_latest_button)

        self.clear_screen_button = QPushButton("清屏")
        self.clear_screen_button.setToolTip("清空当前 GUI 日志")
        self.clear_screen_button.clicked.connect(self.clear_text_box)
        self.clear_screen_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        action_row.addWidget(self.clear_screen_button)

        self.play_latest_wav_button = QPushButton()
        self.play_latest_wav_button.setToolTip("播放最近一次录音 WAV")
        self.play_latest_wav_button.clicked.connect(self.toggle_latest_wav_playback)
        self.play_latest_wav_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.play_latest_wav_button.setFixedSize(28, 28)
        self.play_latest_wav_button.setIconSize(QSize(16, 16))
        action_row.addWidget(self.play_latest_wav_button)

        self.latest_wav_playback_timer = QTimer(self)
        self.latest_wav_playback_timer.timeout.connect(
            self._refresh_latest_wav_playback_state
        )
        self.latest_wav_playback_timer.start(250)
        self._update_latest_wav_playback_button(False)

        context_toggle_row = QHBoxLayout()
        context_toggle_row.setSpacing(10)
        context_toggle_row.setContentsMargins(0, 0, 0, 0)

        self.append_history_checkbox = self._create_context_toggle(
            "最近上屏",
            "控制 LLM 润色时是否附加最近几条已上屏文本作为上下文",
        )
        self.append_history_checkbox.toggled.connect(self.on_append_history_toggled)
        context_toggle_row.addWidget(self.append_history_checkbox)

        self.append_textbox_checkbox = self._create_context_toggle(
            "文本框",
            "控制 LLM 润色时是否附加当前活动文本框全文作为上下文",
        )
        self.append_textbox_checkbox.toggled.connect(self.on_append_textbox_toggled)
        context_toggle_row.addWidget(self.append_textbox_checkbox)

        self.append_vision_checkbox = self._create_context_toggle(
            "视觉",
            "控制 LLM 润色时是否附加当前活动窗口的视觉摘要作为上下文",
        )
        self.append_vision_checkbox.toggled.connect(self.on_append_vision_toggled)
        context_toggle_row.addWidget(self.append_vision_checkbox)

        context_toggle_container = QWidget()
        context_toggle_container.setLayout(context_toggle_row)
        context_toggle_container.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        action_row.addSpacing(8)
        action_row.addWidget(context_toggle_container, 1)
        action_row.addSpacing(8)

        self.edit_lexicon_button = QPushButton("编辑词库")
        self.edit_lexicon_button.setToolTip(
            "编辑用户自定义词库（config/user_lexicon.yaml）"
        )
        self.edit_lexicon_button.clicked.connect(self.show_edit_lexicon_dialog)
        self.edit_lexicon_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        action_row.addWidget(self.edit_lexicon_button)

        uniform_button_width = 144
        for button in (
            self.modify_prompt_button,
            self.edit_polish_prompt_button,
            self.edit_lexicon_button,
        ):
            button.setMinimumWidth(uniform_button_width)
        for button in (self.clear_history_button, self.retry_latest_button):
            button.setMinimumWidth(108)
        self.clear_screen_button.setMinimumWidth(56)

        self._sync_context_toggle_states()

        self.provider_layout.addLayout(provider_row)
        self.provider_layout.addLayout(prompt_action_row)
        self.provider_layout.addLayout(action_row)

        self.sync_asr_realtime_control()

    def sync_asr_realtime_control(self):
        """Show and sync the realtime/file mode switch for supported ASR providers."""
        if not hasattr(self, "asr_realtime_checkbox"):
            return

        visible = False
        checked = False
        if self.provider_manager and hasattr(self, "model_combo"):
            from src.provider.domain import InputMode, ModelRef

            ref = self.model_combo.currentData()
            if isinstance(ref, ModelRef):
                model = self.provider_manager.get_model(ref)
                if model is not None:
                    visible = {
                        InputMode.FILE_UPLOAD,
                        InputMode.LIVE_AUDIO,
                    }.issubset(model.input_modes)
                    checked = (
                        self.provider_manager.get_model_mode(ref)
                        is InputMode.LIVE_AUDIO
                    )

        self.asr_realtime_checkbox.blockSignals(True)
        try:
            self.asr_realtime_checkbox.setChecked(checked)
            self.asr_realtime_checkbox.setVisible(visible)
        finally:
            self.asr_realtime_checkbox.blockSignals(False)

    def _resolve_prompt_text_for_provider(self, provider_id: str) -> str:
        """Determine the prompt text to show in the editor for the given provider."""
        if not self.provider_manager:
            return ""
        provider = self.provider_manager.get_provider(provider_id)
        if not provider:
            return ""

        settings = provider.settings or {}
        inline = settings.get("prompt")
        if isinstance(inline, str) and inline.strip():
            return inline

        preset_name = settings.get("prompt_preset")
        if isinstance(preset_name, str) and preset_name.strip():
            preset_text = self.provider_manager.get_prompt_preset(preset_name.strip())
            if isinstance(preset_text, str) and preset_text.strip():
                return preset_text

        try:
            active_id = getattr(self.provider_manager, "active_provider", None)
            if (
                active_id is not None
                and str(active_id).strip() == str(provider_id).strip()
            ):
                prompt = self.provider_manager.get_provider_prompt()
                if isinstance(prompt, str):
                    return prompt
        except Exception:
            pass

        try:
            from src.provider.provider_config import prompt_manager as _prompt_manager

            default_text = _prompt_manager.get_default_prompt_text()
            if isinstance(default_text, str):
                return default_text
        except Exception:
            pass

        return ""

    def show_modify_prompt_dialog(self) -> None:
        """Open the ASR prompt editor dialog and persist any changes."""
        if not self.provider_manager:
            self.append_colored_line("转录服务商配置系统未初始化", "#ff5555")
            return

        from src.provider.domain import ModelRef

        ref = self.model_combo.currentData() if hasattr(self, "model_combo") else None
        if not isinstance(ref, ModelRef):
            self.append_colored_line("请先选择转录模型", "#ff5555")
            return
        provider_id = ref.provider_id

        initial_text = self._resolve_prompt_text_for_provider(provider_id)

        dialog = PromptEditDialog(
            self, initial_text=initial_text, window_title="编辑 ASR 提示词"
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_prompt_raw = dialog.prompt_text()
        new_prompt_trimmed = new_prompt_raw.strip()

        try:
            if new_prompt_trimmed:
                updated = self.provider_manager.update_provider_prompt(
                    provider_id, prompt=new_prompt_raw
                )
            else:
                updated = self.provider_manager.update_provider_prompt(
                    provider_id, prompt=None
                )
        except Exception:
            updated = False

        if not updated:
            self.append_colored_line(
                "保存 ASR 提示词失败（请检查配置文件权限或格式）", "#ff5555"
            )
            return

        self.restart_children_with_env()

        if new_prompt_trimmed:
            self.append_colored_line("已保存自定义 ASR 提示词。")
        else:
            self.append_colored_line("已清除自定义 ASR 提示词，恢复为预设/默认值。")

    def show_edit_polish_prompt_dialog(self) -> None:
        """Open the LLM polish prompt editor dialog and persist any changes."""
        try:
            initial_text = get_polish_prompt_text()
        except Exception as exc:
            self.append_colored_line(f"读取 LLM 提示词失败：{exc}", "#ff5555")
            return

        dialog = PromptEditDialog(
            self, initial_text=initial_text, window_title="编辑 LLM 提示词"
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            updated = update_polish_prompt_text(dialog.prompt_text())
        except Exception:
            updated = False

        if not updated:
            self.append_colored_line(
                "保存 LLM 提示词失败（请检查 config/polish/polish.yaml 权限或格式）",
                "#ff5555",
            )
            return

        self.restart_children_with_env()
        self.append_colored_line("已保存 LLM 提示词。")

    def show_edit_lexicon_dialog(self) -> None:
        """Load Monaco on first use without blocking the main GUI."""
        if self._lexicon_editor_client.is_ready():
            if not self._lexicon_editor_client.show():
                self.append_colored_line("启动用户词库编辑器失败。", "#ff5555")
            return

        if self._lexicon_loading_dialog is not None:
            self._lexicon_loading_dialog.raise_()
            self._lexicon_loading_dialog.activateWindow()
            return

        loading_dialog = QProgressDialog(
            "正在加载用户词库编辑器，请稍候……",
            "",
            0,
            0,
            self,
        )
        loading_dialog.setWindowTitle("正在加载")
        # Qt supports a null cancel button, although the PySide stub omits it.
        loading_dialog.setCancelButton(cast(QPushButton, None))
        loading_dialog.setAutoClose(False)
        loading_dialog.setAutoReset(False)
        loading_dialog.setMinimumDuration(0)
        loading_dialog.setWindowModality(Qt.WindowModality.WindowModal)
        self._lexicon_loading_dialog = loading_dialog
        loading_dialog.show()

        # Let Qt paint the progress dialog before spawning the helper process.
        QTimer.singleShot(0, self._request_lexicon_editor)

    def _request_lexicon_editor(self) -> None:
        if not self._lexicon_editor_client.show():
            self._close_lexicon_loading_dialog()
            self.append_colored_line("启动用户词库编辑器失败。", "#ff5555")

    def _close_lexicon_loading_dialog(self) -> None:
        dialog = self._lexicon_loading_dialog
        self._lexicon_loading_dialog = None
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()

    def _poll_lexicon_editor_event(self) -> None:
        event = self._lexicon_editor_client.poll_event()
        if not event:
            process = self._lexicon_editor_client.process
            if (
                self._lexicon_loading_dialog is not None
                and process is not None
                and not self._lexicon_editor_client.is_running()
            ):
                self._close_lexicon_loading_dialog()
                self.append_colored_line("用户词库编辑器加载进程意外退出。", "#ff5555")
            return
        event_name = event.get("event")
        if event_name in {"opened", "already_open"}:
            self._close_lexicon_loading_dialog()
        elif event_name == "saved":
            self.append_colored_line("用户词库已保存，下次录音自动生效。")
        elif event_name == "error":
            self._close_lexicon_loading_dialog()
            message = event.get("message") or "未知错误"
            self.append_colored_line(f"读取用户词库失败：{message}", "#ff5555")

    def clear_recent_output_history(self) -> None:
        """Clear the recent finalized-text history used as LLM polish context."""
        try:
            write_clear_history_request()
        except Exception as exc:
            self.append_colored_line(f"清除最近上屏记录失败：{exc}", "#ff5555")
            return

        self.append_colored_line("已请求清除最近上屏消息记录。", "#008000")

    def on_asr_realtime_toggled(self, checked: bool):
        """Persist the selected ASR realtime/file upload mode."""
        if not self.provider_manager:
            return
        from src.provider.domain import InputMode, ModelRef

        ref = self.model_combo.currentData() if hasattr(self, "model_combo") else None
        if not isinstance(ref, ModelRef):
            return
        model = self.provider_manager.get_model(ref)
        if model is None or not {
            InputMode.FILE_UPLOAD,
            InputMode.LIVE_AUDIO,
        }.issubset(model.input_modes):
            return

        ok = False
        try:
            ok = self.provider_manager.set_model_mode(
                ref,
                InputMode.LIVE_AUDIO if checked else InputMode.FILE_UPLOAD,
            )
        except Exception:
            ok = False

        if ok:
            mode = "流式音频" if checked else "录音文件"
            self.append_colored_line(f"语音识别已切换为{mode}模式")
            self.restart_children_with_env()
        else:
            self.append_colored_line(
                "更新语音识别音频模式失败（请检查配置文件权限或格式）",
                "#ff5555",
            )
            self.sync_asr_realtime_control()

    def populate_model_combo(self):
        """Populate a provider-grouped, declarative model catalog."""
        if not hasattr(self, "model_combo"):
            return
        self.model_combo.blockSignals(True)
        try:
            self.model_combo.clear()
            if not self.provider_manager:
                self.model_combo.addItem("转录模型配置系统未初始化", None)
                return
            models = self.provider_manager.list_models() or []
            if not models:
                self.model_combo.addItem("未找到转录模型配置", None)
                return
            active = self.provider_manager.get_active_model_ref()
            groups: dict[str, dict[str, Any]] = {}
            for model_info in models:
                provider_id = str(model_info["provider_id"])
                group = groups.setdefault(
                    provider_id,
                    {
                        "provider_id": provider_id,
                        "provider_name": str(model_info["provider_name"]),
                        "models": [],
                    },
                )
                group["models"].append(model_info)

            active_index: int | None = None
            first_model_index: int | None = None
            item_model = cast(QStandardItemModel, self.model_combo.model())
            sorted_groups = sorted(
                groups.values(),
                key=lambda group: (
                    group["provider_name"].casefold(),
                    group["provider_id"].casefold(),
                ),
            )
            for group in sorted_groups:
                self.model_combo.addItem(group["provider_name"], None)
                header_index = self.model_combo.count() - 1
                header_item = item_model.item(header_index)
                if header_item is not None:
                    header_item.setFlags(
                        header_item.flags() & ~Qt.ItemFlag.ItemIsSelectable
                    )
                    header_font = header_item.font()
                    header_font.setBold(True)
                    header_item.setFont(header_font)

                sorted_models = sorted(
                    group["models"],
                    key=lambda model_info: (
                        str(model_info["name"]).casefold(),
                        str(model_info["model_id"]).casefold(),
                    ),
                )
                for model_info in sorted_models:
                    ref = model_info["ref"]
                    self.model_combo.addItem(f"    {model_info['name']}", ref)
                    model_index = self.model_combo.count() - 1
                    if first_model_index is None:
                        first_model_index = model_index
                    if ref == active:
                        active_index = model_index

            selected_index = active_index
            if selected_index is None:
                selected_index = first_model_index
            if selected_index is not None:
                self.model_combo.setCurrentIndex(selected_index)
            self.log_message(f"转录模型选择器已准备就绪，共 {len(models)} 个选项")
        finally:
            self.model_combo.blockSignals(False)

    def on_model_changed(self, _index: int):
        """Persist the selected provider/model pair and restart workers."""
        if not self.provider_manager:
            return
        from src.provider.domain import ModelRef

        ref = self.model_combo.currentData()
        if not isinstance(ref, ModelRef):
            return
        if ref == self.provider_manager.get_active_model_ref():
            return
        if self.provider_manager.set_active_model(ref):
            resolved = self.provider_manager.resolve_model(ref)
            self.append_colored_line(
                f"已切换转录模型: {resolved.model_name} · {resolved.provider_name}"
            )
            self.sync_asr_realtime_control()
            self.restart_children_with_env()
        else:
            self.append_colored_line(
                "更新转录模型失败（请检查状态文件权限或配置）", "#ff5555"
            )

    def scroll_to_bottom(self):
        """Pin the console view to the latest line after text changes."""
        start = time.perf_counter()
        try:
            sb = self.text_box_client.verticalScrollBar()
            if sb is not None:
                sb.setValue(sb.maximum())
            else:
                # Fallback: ensure cursor at end (rarely needed)
                cursor = self.text_box_client.textCursor()
                cursor.movePosition(QTextCursor.MoveOperation.End)
                self.text_box_client.setTextCursor(cursor)
                self.text_box_client.ensureCursorVisible()
        except Exception:
            pass
        finally:
            elapsed = _elapsed_ms(start)
            if not self._suppress_scroll_timing and elapsed >= GUI_TIMING_SLOW_MS:
                self._log_gui_timing(f"scroll_to_bottom {elapsed:.1f}ms")

    def _resolve_gui_color(self, color: QColor | str | None) -> QColor:
        """Return a valid GUI QColor for Qt and Rich-style color names."""
        if isinstance(color, QColor):
            return color if color.isValid() else QColor("#000000")
        color_text = str(color or "#000000").strip()
        candidates = [color_text]
        candidates.extend(color_text.split(" on ", 1)[0].split())
        for candidate in candidates:
            candidate = GUI_COLOR_ALIASES.get(candidate.lower(), candidate)
            resolved = QColor(candidate)
            if resolved.isValid():
                return resolved
        return QColor("#000000")

    def append_plain_line(self, text: str) -> None:
        self.append_colored_line(text, "#000000")

    def append_colored_lines(
        self, lines: list[str], color: QColor | str = "green"
    ) -> None:
        if not lines:
            return
        text = "\n".join(str(line) for line in lines)
        self.append_colored_line(
            text, color, source=f"batch lines={len(lines)} chars={len(text)}"
        )

    def _append_colored_entries(
        self,
        entries: Sequence[tuple[str, QColor | str]],
        *,
        source: str,
    ) -> None:
        """Append a mixed-color batch with one layout/scroll transaction."""
        if not entries:
            return
        start = time.perf_counter()
        block_count_before = 0
        try:
            document = self.text_box_client.document()
            block_count_before = document.blockCount()
            resolved = [
                (str(text), self._resolve_gui_color(color)) for text, color in entries
            ]
            _append_log_document_entries(document, resolved)
            self.scroll_to_bottom()
        except Exception:
            pass
        finally:
            elapsed = _elapsed_ms(start)
            if not self._suppress_append_timing and elapsed >= GUI_TIMING_SLOW_MS:
                try:
                    block_count_after = self.text_box_client.document().blockCount()
                except Exception:
                    block_count_after = block_count_before
                self._log_gui_timing(
                    f"append log batch {elapsed:.1f}ms {source} "
                    f"blocks={block_count_before}->{block_count_after}"
                )

    def append_colored_line(
        self,
        text: str,
        color: QColor | str = "green",
        *,
        source: str = "single",
    ):
        """Append a plain-text line with an explicit color.

        Use a detached cursor at the document end so user selection and current
        cursor formatting cannot recolor existing text or bleed into new lines.
        """
        self._append_colored_entries([(text, color)], source=source)

    def _log_gui_timing(self, message: str) -> None:
        if self._gui_timing_reports >= 60:
            return
        self._gui_timing_reports += 1
        record_console_message(f"[timing][gui] {message}", style="#888888")

    def _emit_watchdog_timing(self, message: str) -> None:
        try:
            print(f"[timing][watchdog] {message}", flush=True)
        except Exception:
            pass
        if self._gui_timing_reports >= 60:
            return
        self._gui_timing_reports += 1
        record_console_message(f"[timing][watchdog] {message}", style="#888888")

    def _log_queue_from_thread(self, text: str) -> None:
        try:
            self.output_router.route_line(text)
        except Exception:
            pass

    def _gui_watchdog_loop(self) -> None:
        while not self._watchdog_stop.wait(0.1):
            gap_ms = (time.monotonic() - self._last_gui_heartbeat) * 1000.0
            if gap_ms < GUI_WATCHDOG_GAP_MS:
                continue
            if self._watchdog_reports >= 10:
                continue
            self._watchdog_reports += 1
            frame = sys._current_frames().get(self._gui_thread_id)
            if frame is None:
                self._emit_watchdog_timing(
                    f"gui heartbeat gap {gap_ms:.1f}ms; no gui frame"
                )
            else:
                stack = "".join(traceback.format_stack(frame, limit=12)).strip()
                self._emit_watchdog_timing(f"gui heartbeat gap {gap_ms:.1f}ms\n{stack}")
            time.sleep(1.0)

    def retry_latest_request(self) -> None:
        try:
            if not has_retry_audio():
                self.append_colored_line("没有可重试的最近录音。", "#ff8800")
                return
            write_retry_request()
            self.append_colored_line("已请求重试最近一次录音。", "#008000")
        except Exception as exc:
            self.append_colored_line(f"请求重试失败：{exc}", "#ff5555")

    def _latest_wav_path(self) -> Path:
        return latest_audio_path_for_mime("audio/wav")

    def _is_latest_wav_playing(self) -> bool:
        player = self._latest_wav_player
        return player is not None and player.poll() is None

    def _update_latest_wav_playback_button(self, playing: bool) -> None:
        style = self.style()
        icon = style.standardIcon(
            QStyle.StandardPixmap.SP_MediaStop
            if playing
            else QStyle.StandardPixmap.SP_MediaVolume
        )
        self.play_latest_wav_button.setIcon(icon)
        self.play_latest_wav_button.setToolTip(
            "停止播放最近一次录音 WAV" if playing else "播放最近一次录音 WAV"
        )
        self.play_latest_wav_button.setAccessibleName(
            "停止播放最近录音" if playing else "播放最近录音"
        )

    def _refresh_latest_wav_playback_state(self) -> None:
        if (
            self._latest_wav_player is not None
            and self._latest_wav_player.poll() is not None
        ):
            self._latest_wav_player = None
        self._update_latest_wav_playback_button(self._is_latest_wav_playing())

    def _stop_latest_wav_playback(self) -> None:
        player = self._latest_wav_player
        self._latest_wav_player = None
        if player is not None and player.poll() is None:
            try:
                player.terminate()
                try:
                    player.wait(timeout=1.0)
                except subprocess.TimeoutExpired:
                    player.kill()
            except Exception:
                pass
        self._update_latest_wav_playback_button(False)

    def toggle_latest_wav_playback(self) -> None:
        if self._is_latest_wav_playing():
            self._stop_latest_wav_playback()
            return

        wav_path = self._latest_wav_path()
        if (
            not wav_path.exists()
            or not wav_path.is_file()
            or wav_path.stat().st_size <= 0
        ):
            self.append_colored_line("没有可播放的 latest.wav。", "#ff8800")
            self._update_latest_wav_playback_button(False)
            return

        ffplay = _resolve_ffplay_exe()
        if ffplay is None:
            self.append_colored_line("未找到 ffplay，无法播放 latest.wav。", "#ff5555")
            self._update_latest_wav_playback_button(False)
            return

        self._stop_latest_wav_playback()

        command = [
            ffplay,
            "-nodisp",
            "-v",
            "quiet",
            "-autoexit",
            str(wav_path),
        ]
        try:
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            startupinfo.wShowWindow = subprocess.SW_HIDE
            self._latest_wav_player = subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_NO_WINDOW,
                startupinfo=startupinfo,
            )
            self._update_latest_wav_playback_button(True)
        except Exception as exc:
            self._latest_wav_player = None
            self._update_latest_wav_playback_button(False)
            self.append_colored_line(f"播放 latest.wav 失败：{exc}", "#ff5555")

    def abandon_current_task(self) -> None:
        try:
            write_abandon_request()
            self.status_overlay.hide_all()
            self.append_colored_line("已请求放弃当前任务。", "#cc4444")
        except Exception as exc:
            self.append_colored_line(f"请求放弃失败：{exc}", "#ff5555")

    def show_startup_info(self):
        """Show startup information using YAML-based provider configuration."""
        if not self.provider_manager:
            self.append_plain_line("转录服务商配置系统未初始化")
            self.append_plain_line("================")
            return

        active = self.provider_manager.get_active_model()
        if active:
            self.append_plain_line(
                f"转录模型: {active.model_name} · {active.provider_name}"
            )
            self.append_plain_line(f"转录适配器: {active.adapter_type}")
            self.append_plain_line(f"上游模型: {active.upstream_model}")
            self.append_plain_line(f"音频模式: {active.input_mode.value}")
        else:
            self.append_plain_line("转录模型: 未配置")

        self.append_plain_line("================")

    def create_systray_icon(self):
        """Start the independent process that owns the tray icon and menu."""
        if not self._tray_process_client.start():
            self.log_message("启动托盘进程失败。", "#ff0000")

    def _poll_tray_process_event(self) -> None:
        if not self._tray_process_client.is_running():
            self._tray_process_client.start()
            return

        event = self._tray_process_client.poll_event()
        if event is None:
            return

        event_name = event.get("event")
        if event_name == "show":
            self._show_from_tray()
        elif event_name == "reload_providers":
            self.reload_providers()
        elif event_name == "restart_client":
            self.restart_client()
        elif event_name == "quit":
            self.quit_app()

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def restart_client(self):
        # Important: run the restart helper with the console Python (python.exe),
        # not pythonw.exe. Otherwise the helper would be killed by its own taskkill.
        exe = resolve_console_python()
        try:
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                [exe, str(restart_script_path())],
                creationflags=(
                    subprocess.CREATE_NO_WINDOW
                    | DETACHED_PROCESS
                    | CREATE_NEW_PROCESS_GROUP
                ),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                cwd=str(ROOT),
            )
        except Exception as e:
            # Surface the error but keep GUI alive
            try:
                self.append_plain_line(f"重启失败: {e}")
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

    def update_word_count_toggled(self):
        select_text_count = len(self.text_box_client.textCursor().selectedText())
        select_text_bytes = len(
            self.text_box_client.textCursor().selectedText().encode("utf-8")
        )
        total_text_count = len(self.text_box_client.toPlainText())
        total_text_bytes = len(self.text_box_client.toPlainText().encode("utf-8"))
        unselect_text_count = total_text_count - select_text_count
        unselect_text_bytes = total_text_bytes - select_text_bytes
        if self.text_box_wordCountLabel is not None:
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
                self.populate_model_combo()
                self.sync_asr_realtime_control()
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
                self.append_plain_line(f"打开目录失败: {e}")
            except Exception:
                pass

    def closeEvent(self, event):
        # Minimize to system tray instead of closing the window when the user clicks the close button
        self._stop_latest_wav_playback()
        self.hide()  # Hide the window
        event.ignore()  # Ignore the close event

    def quit_app(self):
        try:
            self._watchdog_stop.set()
        except Exception:
            pass
        try:
            self.status_overlay.hide_all()
        except Exception:
            pass
        self._stop_latest_wav_playback()
        self._lexicon_editor_client.stop()
        self._tray_process_client.stop()
        # Terminate core_client.py and any launcher-spawned child processes from this checkout.
        self._stop_core_client_processes()

        # Quit the application
        QApplication.quit()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()  # Press ESC to hide main window

    def start_script(self):
        # Start core_client.py and redirect output to the client queue
        exe = resolve_pythonw_client()
        if exe is None:
            try:
                self.append_plain_line(
                    "未找到可用的 Python 运行时。请确保已运行 'uv sync' 安装依赖。"
                )
            except Exception:
                pass
            return

        # Core first using common worker launcher
        try:
            self._start_worker("core_client.py", "core_client_process")
        except Exception:
            pass

        # Update text box
        try:
            self.update_timer = QTimer()
            self.update_timer.timeout.connect(self.update_worker_output)
            self.update_timer.start(50)
        except Exception:
            pass

    def update_worker_output(self):
        total_start = time.perf_counter()
        now = time.monotonic()
        self._last_gui_heartbeat = now
        if self._last_worker_timer_tick:
            tick_gap_ms = (now - self._last_worker_timer_tick) * 1000.0
            if tick_gap_ms >= GUI_TIMER_GAP_MS and self._worker_timer_lag_reports < 20:
                self._worker_timer_lag_reports += 1
                self._log_gui_timing(f"worker timer gap {tick_gap_ms:.1f}ms")
        self._last_worker_timer_tick = now

        level_start = time.perf_counter()
        latest_level = self.output_router.take_latest_overlay_level()
        if latest_level is not None:
            self.status_overlay.set_level(latest_level)
        level_ms = _elapsed_ms(level_start)

        drain_start = time.perf_counter()
        batch_entries: list[tuple[str, str]] = []
        line_count = 0
        char_count = 0
        for line in self.output_router.take_log_lines_for(200, 0.004):
            line_count += 1
            char_count += len(line.text)
            color = line.color or "#000000"
            if batch_entries and color == batch_entries[-1][1]:
                previous_text, previous_color = batch_entries[-1]
                batch_entries[-1] = (f"{previous_text}\n{line.text}", previous_color)
            else:
                batch_entries.append((line.text, color))
        if batch_entries:
            self._append_colored_entries(
                batch_entries,
                source=f"worker lines={line_count} chars={char_count}",
            )
        drain_ms = _elapsed_ms(drain_start)
        total_ms = _elapsed_ms(total_start)
        if total_ms >= GUI_TIMING_SLOW_MS or drain_ms >= GUI_TIMING_SLOW_MS:
            self._log_gui_timing(
                f"update_worker_output total={total_ms:.1f}ms "
                f"level={level_ms:.1f}ms logs={drain_ms:.1f}ms "
                f"lines={line_count} chars={char_count}"
            )

    def _handle_status_overlay_event(self, payload: dict) -> None:
        total_start = time.perf_counter()
        try:
            received_at = payload.get("emitted_at")
            if received_at is not None:
                delay_ms = max(0.0, (time.time() - float(received_at)) * 1000.0)
                if delay_ms >= GUI_TIMER_GAP_MS:
                    self._log_gui_timing(
                        f"overlay event delay {delay_ms:.1f}ms "
                        f"action={payload.get('action')} state={payload.get('state')}"
                    )
            action = payload.get("action")
            state = payload.get("state")
            handling_start = time.perf_counter()
            if action == "level":
                self.status_overlay.set_level(float(payload.get("level", 0.0)))
            elif action == "show" and state == "listening":
                self.status_overlay.show_listening()
            elif action == "show" and state in {"transcribing", "polishing"}:
                self.status_overlay.show_processing(str(state))
            elif action == "show":
                self.status_overlay.show_listening()
            elif action == "hide":
                self.status_overlay.hide_all()
            handling_ms = _elapsed_ms(handling_start)
            total_ms = _elapsed_ms(total_start)
            if total_ms >= GUI_TIMING_SLOW_MS or handling_ms >= GUI_TIMING_SLOW_MS:
                self._log_gui_timing(
                    f"overlay handler total={total_ms:.1f}ms "
                    f"handle={handling_ms:.1f}ms action={action} state={state}"
                )
        except Exception:
            pass

    def _handle_context_toggle_event(self, payload: dict) -> None:
        try:
            if payload.get("target") != "textbox_context":
                return
            enabled = bool(payload.get("enabled"))
            self._syncing_context_toggle_states = True
            try:
                self.append_textbox_checkbox.setChecked(enabled)
            finally:
                self._syncing_context_toggle_states = False
        except Exception:
            pass

    # ============ Worker restart utilities ============

    def _stop_core_client_processes(self) -> None:
        self._stop_process(getattr(self, "core_client_process", None), "core_client")
        try:
            terminate_python_script_processes(core_client_script_path())
        except Exception:
            pass
        self.core_client_process = None

    def _stop_process(self, proc: subprocess.Popen | None, name: str) -> None:
        if not proc:
            return
        try:
            if proc.poll() is None:
                if os.name == "nt":
                    try:
                        subprocess.run(
                            ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                            creationflags=subprocess.CREATE_NO_WINDOW,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            stdin=subprocess.DEVNULL,
                            timeout=2.0,
                            check=False,
                        )
                    except Exception:
                        proc.terminate()
                else:
                    proc.terminate()
                try:
                    proc.wait(timeout=0.8)
                except Exception:
                    pass
                if proc.poll() is None:
                    proc.kill()
        except Exception:
            pass

    def _start_worker(
        self, script_rel_path: str, attr_name: str, *, log_errors: bool = True
    ) -> bool:
        exe = resolve_pythonw_client()
        if exe is None:
            if log_errors:
                self.append_plain_line("未找到可用的 Python 运行时。无法重启子进程。")
            return False
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
                target=self.output_router.read_stream,
                args=(p.stdout,),
                daemon=True,
            ).start()
            return True
        except Exception as e:
            if log_errors:
                self.append_plain_line(f"启动子进程失败({script_rel_path}): {e}")
            return False

    def restart_children_with_env(self) -> None:
        """Restart worker subprocesses without blocking the Qt event loop."""
        try:
            self.status_overlay.hide_all()
        except Exception:
            pass
        with self._worker_restart_lock:
            if self._worker_restart_running:
                self._worker_restart_pending = True
                self._log_queue_from_thread("录音进程正在重启，已合并新的重启请求。")
                return
            self._worker_restart_running = True

        threading.Thread(
            target=self._restart_children_with_env_worker,
            name="capswriter-worker-restart",
            daemon=True,
        ).start()

    def _restart_children_with_env_worker(self) -> None:
        try:
            while True:
                self._stop_core_client_processes()
                if not self._start_worker(
                    "core_client.py", "core_client_process", log_errors=False
                ):
                    self._log_queue_from_thread("启动子进程失败(core_client.py)。")
                    with self._worker_restart_lock:
                        self._worker_restart_running = False
                        self._worker_restart_pending = False
                    return
                with self._worker_restart_lock:
                    if self._worker_restart_pending:
                        self._worker_restart_pending = False
                        continue
                    self._worker_restart_running = False
                    return
        except Exception as exc:
            self._log_queue_from_thread(f"重启录音进程失败: {exc}")
            with self._worker_restart_lock:
                self._worker_restart_running = False
                self._worker_restart_pending = False

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.old_pos = event.globalPosition().toPoint()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            delta = QPoint(event.globalPosition().toPoint() - self.old_pos)
            self.move(self.x() + delta.x(), self.y() + delta.y())
            self.old_pos = event.globalPosition().toPoint()

    def checkWindowInfo(self):
        geometry = self.geometry()
        x = geometry.x()
        y = geometry.y()
        width = geometry.width()
        height = geometry.height()
        primaryScreen = QGuiApplication.primaryScreen()
        if primaryScreen is None:
            return x, y, width, height, 0, 0
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
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
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
        widgets: list[QWidget] = [self.text_box_client]
        if hasattr(self, "daily_input_count_label"):
            widgets.append(self.daily_input_count_label)
        if hasattr(self, "modify_prompt_button"):
            widgets.append(self.modify_prompt_button)
        if hasattr(self, "edit_polish_prompt_button"):
            widgets.append(self.edit_polish_prompt_button)
        if hasattr(self, "edit_lexicon_button"):
            widgets.append(self.edit_lexicon_button)
        if hasattr(self, "clear_history_button"):
            widgets.append(self.clear_history_button)
        if hasattr(self, "retry_latest_button"):
            widgets.append(self.retry_latest_button)
        if hasattr(self, "clear_screen_button"):
            widgets.append(self.clear_screen_button)
        if hasattr(self, "play_latest_wav_button"):
            widgets.append(self.play_latest_wav_button)
        if hasattr(self, "model_combo"):
            widgets.append(self.model_combo)
        if hasattr(self, "model_label"):
            widgets.append(self.model_label)

        for widget in widgets:
            # 检查字体大小是否已设置，如果没有设置，则使用一个默认值
            current_font = widget.font()
            if current_font.pointSizeF() < 9:
                current_font.setPointSizeF(9)  # 设置一个默认字体大小
            current_font.setPointSizeF(current_font.pointSizeF() * self.scale_factor)
            widget.setFont(current_font)


def start_client_gui(profile_options: StartupProfileOptions | None = None):
    def replace_existing_gui() -> None:
        terminate_python_script_basename_processes(
            ROOT / "start_client_gui.py", exclude_pid=os.getpid()
        )
        terminate_executable_processes(
            ROOT / "start_client_gui.exe", exclude_pid=os.getpid()
        )
        terminate_executable_processes(
            ROOT / "start_client_gui_admin.exe", exclude_pid=os.getpid()
        )
        terminate_python_script_processes(
            core_client_script_path(), exclude_pid=os.getpid()
        )

    startup_slot_acquired = prepare_replacement_startup(
        ROOT, "client_gui", replace_existing_gui
    )
    if not startup_slot_acquired:
        print("无法完成 CapsWriter GUI 替换，本次启动已退出。")
        return
    startup_profiler = StartupProfiler(profile_options)
    try:
        startup_profiler.start()
        app = QApplication(sys.argv)
        configure_app_locale_and_font(app, GUI._preferred_cn_font_family())
        # Defer theme application to improve first paint time
        apply_theme_later(app)
        # Print screen info after Qt app is initialized (accurate in multi-monitor setups)
        try:
            global scale_x, scale_y
            scale_x, scale_y = print_screen_scale()
        except Exception as e:
            # Don't block startup if printing screen info fails
            print(f"print_screen_scale error: {e}")
        global gui
        gui = GUI()
        if not Config.shrink_automatically_to_tray:
            gui.show()
        try:
            if startup_profiler.enabled:
                delay = max(
                    100, int((profile_options.duration_ms if profile_options else 5000))
                )
                QTimer.singleShot(
                    delay, lambda: startup_profiler.stop("startup window")
                )
        except Exception:
            startup_profiler.stop("timer schedule failed")
        sys.exit(app.exec())
    finally:
        release_startup_slot(ROOT, "client_gui")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="处理文件")
    parser.add_argument("files", nargs="*", type=Path, help="要处理的文件")
    parser.add_argument("--lexicon-editor-process", type=Path, default=None)
    parser.add_argument("--lexicon-parent-pid", type=int, default=None)
    parser.add_argument("--tray-process", type=Path, default=None)
    parser.add_argument("--tray-parent-pid", type=int, default=None)
    parser.add_argument("--file-list", type=Path, help="包含文件列表的文本文件")
    parser.add_argument(
        "--profile-startup",
        action="store_true",
        help="启用启动性能分析（输出到 profiles/startup）",
    )
    parser.add_argument(
        "--profile-tool",
        choices=["cprofile", "pyinstrument", "viztracer", "yappi"],
        default=None,
        help="选择性能分析工具",
    )
    parser.add_argument(
        "--profile-output",
        type=Path,
        default=None,
        help="指定分析输出文件前缀（不带扩展名）",
    )
    parser.add_argument(
        "--profile-duration-ms",
        type=int,
        default=int(os.getenv("CW_PROFILE_DURATION_MS", "5000")),
        help="启动分析持续时间（毫秒）",
    )
    args = parser.parse_args()

    if args.lexicon_editor_process is not None:
        from src.gui.lexicon_editor_process import run_lexicon_editor_process

        if args.lexicon_parent_pid is None:
            parser.error(
                "--lexicon-parent-pid is required with --lexicon-editor-process"
            )
        raise SystemExit(
            run_lexicon_editor_process(
                args.lexicon_editor_process, args.lexicon_parent_pid
            )
        )

    if args.tray_process is not None:
        from src.gui.tray_process import run_tray_process

        if args.tray_parent_pid is None:
            parser.error("--tray-parent-pid is required with --tray-process")
        raise SystemExit(run_tray_process(args.tray_process, args.tray_parent_pid))

    profile_env_enabled = os.getenv("CW_PROFILE_STARTUP", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    profile_enabled = bool(args.profile_startup or profile_env_enabled)
    profile_tool = (
        args.profile_tool
        or os.getenv("CW_PROFILE_TOOL", "cprofile").strip().lower()
        or "cprofile"
    )
    profile_output_env = os.getenv("CW_PROFILE_OUTPUT", "").strip()
    profile_output = args.profile_output or (
        Path(profile_output_env) if profile_output_env else None
    )
    profile_options = StartupProfileOptions(
        enabled=profile_enabled,
        tool=profile_tool,
        output=profile_output,
        duration_ms=max(100, int(args.profile_duration_ms or 5000)),
    )

    if args.file_list:  # 如果传递了 --file-list 参数
        try:
            files = read_file_list(args.file_list)
        except Exception as e:
            print(f"Error reading file list: {e}")
            sys.exit(1)
    else:
        files = args.files  # 直接传递的文件列表

    if files:  # 如果有文件需要处理
        script_path = core_client_script_path()
        python_exe_path = resolve_console_python()
        files_quoted = [str(file) for file in files]
        command = [python_exe_path, str(script_path)] + files_quoted
        try:
            subprocess.Popen(command, cwd=str(ROOT))
        except Exception as e:
            print(f"Error starting the process: {e}")
    else:
        # GUI
        start_client_gui(profile_options=profile_options)
