import argparse
import os
import re
import subprocess
import sys
import threading
import asyncio
from pathlib import Path
from queue import Queue
from typing import Any, cast

import yaml

from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QIcon,
    QWheelEvent,
    QTextOption,
    QTextCursor,
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
    QMenu,
    QPushButton,
    QSizePolicy,
    QSystemTrayIcon,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)
# Intentionally defer theme import/application until after first paint for faster startup

from src.gui.runtime import (
    ROOT,
    availability_test_script_path,
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

from src.infra.config import ClientConfig as Config
from src.audio.retry_cache import has_retry_audio, write_retry_request
from src.gui.app_startup import apply_theme_later, configure_app_locale_and_font, print_screen_scale
from src.gui.listening_overlay import StatusOverlayController
from src.gui.prompt_editor import PromptEditDialog
from src.gui.startup_profiler import StartupProfileOptions, StartupProfiler
from src.polish.llm_polish import get_polish_prompt_text, reload_polish_config, update_polish_prompt_text
from src.system.process_cleanup import terminate_python_script_processes
from src.system.single_instance import acquire_single_instance, release_single_instance


# AHK hint tooltip removed for leaner startup


class GUI(QMainWindow):
    def __init__(self):
        super().__init__()

        # Queue to store early log messages before UI is ready
        self.early_messages: list[tuple[str, str]] = []
        # Ensure provider_manager attribute exists before UI uses it
        self.provider_manager: Any | None = None
        self._syncing_context_toggle_states = False
        self.core_client_process: subprocess.Popen[str] | None = None
        self.text_box_wordCountLabel: QLabel | None = None
        self.old_pos = QPoint()

        self.init_ui()
        self.output_queue_client: Queue[str] = Queue()
        self.status_overlay = StatusOverlayController()
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
            from src.provider.provider_config import provider_manager
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
        self.main_layout.addWidget(self.text_box_client)
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
            self.populate_prompt_combo()
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
        self.text_box_client.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.text_box_client.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Treat content strictly as plain text to avoid HTML rendering side-effects
        self.text_box_client.setAcceptRichText(False)
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
        self.text_box_client.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.text_box_client.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        # Always follow the latest output (auto-scroll to the bottom on new text)
        try:
            self.text_box_client.textChanged.connect(self.scroll_to_bottom)
        except Exception:
            pass

    def _configure_collapsible_combo(self, combo: QComboBox, *, editable: bool = False) -> None:
        """Apply a unified style and sizing policy to combo boxes."""
        combo.setEditable(editable)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        combo.setMinimumWidth(0)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(0)
        combo.setStyleSheet("QComboBox { min-width: 0px; }")

    def _inline_prompt_label(self, prompt_text: str) -> str:
        """Create a compact label for inline prompts using the first non-empty snippet."""
        snippet_parts = [line.strip() for line in prompt_text.splitlines() if line.strip()]
        snippet = " ".join(snippet_parts)
        if len(snippet) > 18:
            snippet = snippet[:16] + "…"
        return f"自定义: {snippet}" if snippet else "自定义提示词"

    def _build_combo_field(self, label_text: str, combo: QComboBox) -> tuple[QWidget, QLabel]:
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

    def _update_yaml_bool(self, path: Path, key_path: tuple[str, ...], value: bool) -> bool:
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
                newline = line[len(stripped):]
                lines[idx] = f"{match.group('prefix')}{replacement}{match.group('suffix')}{newline}"
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

                newline = line[len(stripped):]
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

    def _load_bool_from_yaml(self, path: Path, key_path: tuple[str, ...], default: bool) -> bool:
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
                self._load_bool_from_yaml(self._polish_config_path(), ("history", "enabled"), True)
            )
            self.append_textbox_checkbox.setChecked(
                self._load_bool_from_yaml(self._polish_config_path(), ("textbox_context", "enabled"), True)
            )
            self.append_vision_checkbox.setChecked(
                self._load_bool_from_yaml(self._vision_config_path(), ("enabled",), False)
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
            self.append_colored_line(f"保存{label}开关失败（请检查配置文件权限或格式）", "#ff5555")
            self._sync_context_toggle_states()
            return

        if path == self._polish_config_path():
            try:
                reload_polish_config()
            except Exception:
                pass

        state_text = "启用" if checked else "禁用"
        self.append_colored_line(f"已{state_text}{label}。")

        if restart_workers:
            self.append_colored_line("正在重启录音进程以应用视觉上下文设置。", "#888888")
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

        self.provider_combo = QComboBox()
        self._configure_collapsible_combo(self.provider_combo)
        provider_field, self.provider_label = self._build_combo_field("转录服务商:", self.provider_combo)
        self.populate_provider_combo()
        self.provider_combo.currentTextChanged.connect(self.on_provider_changed)
        provider_row.addWidget(provider_field, 1)

        self.modify_prompt_button = QPushButton("编辑 ASR 提示词")
        self.modify_prompt_button.setToolTip("编辑当前转录服务商的自定义提示词")
        self.modify_prompt_button.clicked.connect(self.show_modify_prompt_dialog)
        self.modify_prompt_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        provider_row.addWidget(self.modify_prompt_button)

        prompt_row = QHBoxLayout()
        prompt_row.setSpacing(6)
        prompt_row.setContentsMargins(0, 0, 0, 0)

        self.prompt_combo = QComboBox()
        self._configure_collapsible_combo(self.prompt_combo)
        self.prompt_combo.setEditable(False)
        self.prompt_combo.currentIndexChanged.connect(self.on_prompt_changed)
        prompt_field, self.prompt_label = self._build_combo_field("ASR 提示词:", self.prompt_combo)
        prompt_row.addWidget(prompt_field, 1)

        self.edit_polish_prompt_button = QPushButton("编辑 LLM 提示词")
        self.edit_polish_prompt_button.setToolTip("编辑 config/polish/polish.yaml 中的 LLM 润色提示词")
        self.edit_polish_prompt_button.clicked.connect(self.show_edit_polish_prompt_dialog)
        self.edit_polish_prompt_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        prompt_row.addWidget(self.edit_polish_prompt_button)

        action_row = QHBoxLayout()
        action_row.setSpacing(6)
        action_row.setContentsMargins(0, 0, 0, 0)

        self.clear_history_button = QPushButton("清除最近上屏")
        self.clear_history_button.setToolTip("暂时清除 LLM 润色使用的最近上屏消息记录")
        self.clear_history_button.clicked.connect(self.clear_recent_output_history)
        self.clear_history_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        action_row.addWidget(self.clear_history_button)

        self.retry_latest_button = QPushButton("重试最近请求")
        self.retry_latest_button.setToolTip("重新发送最近一次录音缓存，并将结果照常上屏")
        self.retry_latest_button.clicked.connect(self.retry_latest_request)
        self.retry_latest_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        action_row.addWidget(self.retry_latest_button)

        self.clear_screen_button = QPushButton("清屏")
        self.clear_screen_button.setToolTip("清空当前 GUI 日志")
        self.clear_screen_button.clicked.connect(self.clear_text_box)
        self.clear_screen_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        action_row.addWidget(self.clear_screen_button)

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
        context_toggle_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        action_row.addSpacing(8)
        action_row.addWidget(context_toggle_container, 1)
        action_row.addSpacing(8)

        self.edit_lexicon_button = QPushButton("编辑词库")
        self.edit_lexicon_button.setToolTip("编辑用户自定义词库（config/user_lexicon.yaml）")
        self.edit_lexicon_button.clicked.connect(self.show_edit_lexicon_dialog)
        self.edit_lexicon_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
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

        # Model row (only for OpenAI-type providers)
        self.model_row = QHBoxLayout()
        self.model_row.setSpacing(6)
        self.model_row.setContentsMargins(0, 0, 0, 0)
        self.model_combo = QComboBox()
        self._configure_collapsible_combo(self.model_combo, editable=True)
        model_field, self.model_label = self._build_combo_field("模型:", self.model_combo)
        self.model_combo.currentTextChanged.connect(self.on_model_changed)
        self.model_row.addWidget(model_field, 1)
        self.model_row.addStretch()
        model_field.setVisible(False)
        self.model_container = model_field

        self.provider_layout.addLayout(provider_row)
        self.provider_layout.addLayout(prompt_row)
        self.provider_layout.addLayout(action_row)
        self.provider_layout.addLayout(self.model_row)

        # Populate initial lists according to active provider
        self.populate_model_combo()
        self.populate_prompt_combo()

    def populate_prompt_combo(self):
        """Populate the prompt preset dropdown from prompts.yaml and current provider state."""
        if not hasattr(self, "prompt_combo"):
            return
        self.prompt_combo.blockSignals(True)
        try:
            self.prompt_combo.clear()
            # Default item: use global default preset
            self.prompt_combo.addItem("使用全局默认", "__DEFAULT__")

            # Load preset names from provider manager
            presets = {}
            try:
                from src.provider.provider_config import provider_manager as _pm
                if hasattr(_pm, "list_prompt_presets"):
                    presets = _pm.list_prompt_presets() or {}
            except Exception:
                presets = {}

            # Keep insertion order
            for name, meta in presets.items():
                label = str(name)
                self.prompt_combo.addItem(label, label)
                # Tooltip preview of text
                try:
                    text = meta.get("text") if isinstance(meta, dict) else None
                    if isinstance(text, str) and text.strip():
                        preview = " ".join(line.strip() for line in text.splitlines() if line.strip())
                        if len(preview) > 160:
                            preview = preview[:157] + "..."
                        idx = self.prompt_combo.count() - 1
                        self.prompt_combo.setItemData(idx, preview, Qt.ItemDataRole.ToolTipRole)
                except Exception:
                    pass

            inline_prompt: str | None = None
            current_preset: str | None = None
            current_data = None
            try:
                current_data = self.provider_combo.currentData()
                if self.provider_manager and current_data is not None:
                    provider = self.provider_manager.get_provider(current_data)
                    if provider and hasattr(provider, "settings"):
                        settings = provider.settings or {}
                        raw_inline = settings.get("prompt")
                        if isinstance(raw_inline, str) and raw_inline.strip():
                            inline_prompt = raw_inline
                        preset_value = settings.get("prompt_preset")
                        if isinstance(preset_value, str) and preset_value.strip():
                            current_preset = preset_value.strip()
            except Exception:
                pass

            selected_index = 0
            if inline_prompt:
                label = self._inline_prompt_label(inline_prompt)
                custom_index = self.prompt_combo.count()
                self.prompt_combo.addItem(label, "__INLINE__")
                self.prompt_combo.setItemData(custom_index, inline_prompt, Qt.ItemDataRole.ToolTipRole)
                selected_index = custom_index
            elif isinstance(current_preset, str) and current_preset:
                for i in range(self.prompt_combo.count()):
                    if self.prompt_combo.itemData(i) == current_preset:
                        selected_index = i
                        break

            self.prompt_combo.setCurrentIndex(selected_index)
        finally:
            self.prompt_combo.blockSignals(False)

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
            if active_id is not None and str(active_id).strip() == str(provider_id).strip():
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

        provider_id = self.provider_combo.currentData() if hasattr(self, "provider_combo") else None
        if not provider_id:
            self.append_colored_line("请先选择转录服务商", "#ff5555")
            return

        try:
            selection_value = self.prompt_combo.itemData(self.prompt_combo.currentIndex())
        except Exception:
            selection_value = None

        preset_name: str | None = None
        if isinstance(selection_value, str) and selection_value not in {"__INLINE__", "__DEFAULT__"}:
            preset_name = selection_value

        if preset_name:
            initial_text = self.provider_manager.get_prompt_preset(preset_name) or ""
        else:
            initial_text = self._resolve_prompt_text_for_provider(provider_id)

        dialog = PromptEditDialog(self, initial_text=initial_text, window_title="编辑 ASR 提示词")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        new_prompt_raw = dialog.prompt_text()
        new_prompt_trimmed = new_prompt_raw.strip()

        if preset_name:
            updated = False
            try:
                updated = self.provider_manager.update_prompt_preset_text(preset_name, new_prompt_raw)
            except Exception:
                updated = False

            if not updated:
                self.append_colored_line("保存提示词预设失败（请检查 prompts.yaml 权限或格式）", "#ff5555")
                return

            provider_updated = True
            try:
                provider_updated = self.provider_manager.update_provider_prompt(
                    provider_id,
                    prompt=None,
                    prompt_preset=preset_name,
                )
            except Exception:
                provider_updated = False

            self.populate_prompt_combo()
            self.restart_children_with_env()

            msg = f"已更新 ASR 提示词预设: {preset_name}"
            if not provider_updated:
                msg += "（但未能刷新当前服务商设置，请手动检查配置）"
            self.append_colored_line(msg)
            return

        # Inline or default-backed prompt editing falls back to provider-specific overrides
        try:
            if new_prompt_trimmed:
                updated = self.provider_manager.update_provider_prompt(provider_id, prompt=new_prompt_raw)
            else:
                updated = self.provider_manager.update_provider_prompt(provider_id, prompt=None)
        except Exception:
            updated = False

        if not updated:
            self.append_colored_line("保存 ASR 提示词失败（请检查配置文件权限或格式）", "#ff5555")
            return

        self.populate_prompt_combo()
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

        dialog = PromptEditDialog(self, initial_text=initial_text, window_title="编辑 LLM 提示词")
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return

        try:
            updated = update_polish_prompt_text(dialog.prompt_text())
        except Exception:
            updated = False

        if not updated:
            self.append_colored_line("保存 LLM 提示词失败（请检查 config/polish/polish.yaml 权限或格式）", "#ff5555")
            return

        self.restart_children_with_env()
        self.append_colored_line("已保存 LLM 提示词。")

    def show_edit_lexicon_dialog(self) -> None:
        """Open the user lexicon editor and persist any changes."""
        try:
            from src.gui.lexicon_editor import open_lexicon_editor

            accepted, _saved_text = open_lexicon_editor(self)
        except Exception as exc:
            self.append_colored_line(f"读取用户词库失败：{exc}", "#ff5555")
            return

        if not accepted:
            return

        self.append_colored_line("用户词库已保存，下次录音自动生效。")

    def clear_recent_output_history(self) -> None:
        """Clear the recent finalized-text history used as LLM polish context."""
        try:
            from src.polish.llm_polish import clear_finalized_history

            cleared = clear_finalized_history()
        except Exception as exc:
            self.append_colored_line(f"清除最近上屏记录失败：{exc}", "#ff5555")
            return

        if cleared > 0:
            self.append_colored_line(f"已清除最近上屏消息记录：{cleared} 条。")
        else:
            self.append_colored_line("最近上屏消息记录本来就是空的。", "#888888")

    def on_prompt_changed(self, index: int):
        """Handle prompt preset selection change and persist."""
        if not self.provider_manager:
            return
        current_data = self.provider_combo.currentData() if hasattr(self, "provider_combo") else None
        if current_data is None:
            return
        try:
            value = self.prompt_combo.itemData(index)
        except Exception:
            value = None
        ok = False
        try:
            if value == "__DEFAULT__":
                ok = self.provider_manager.update_provider_prompt(current_data, prompt=None, prompt_preset=None)
            elif value == "__INLINE__":
                # Inline prompts are managed via the modify dialog; no change needed.
                return
            elif isinstance(value, str) and value:
                ok = self.provider_manager.update_provider_prompt(current_data, prompt_preset=value)
        except Exception:
            ok = False
        if ok:
            self.append_colored_line(f"已切换提示词预置: {self.prompt_combo.currentText()}")
            self.restart_children_with_env()
        else:
            self.append_colored_line("更新提示词失败（请检查配置文件权限或格式）", "#ff5555")

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
                # Refresh prompt selector values
                self.populate_prompt_combo()

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
        if hasattr(self, "model_container"):
            self.model_container.setVisible(False)

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
        if hasattr(self, "model_container"):
            self.model_container.setVisible(True)

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
            from src.provider.availability_test import run_availability_test

            # Create a new event loop for this thread
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

            try:
                results = loop.run_until_complete(run_availability_test(test_audio_path))

                # Format and display results
                from src.provider.availability_test import ProviderAvailabilityTester
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

            if hasattr(self, "test_all_button"):
                test_all_button = cast(QPushButton, getattr(self, "test_all_button"))
                test_all_button.setEnabled(True)
                test_all_button.setText("Test All")

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
                cursor.movePosition(QTextCursor.MoveOperation.End)
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

    def retry_latest_request(self) -> None:
        try:
            if not has_retry_audio():
                self.append_colored_line("没有可重试的最近录音。", "#ff8800")
                return
            write_retry_request()
            self.append_colored_line("已请求重试最近一次录音。", "#008000")
        except Exception as exc:
            self.append_colored_line(f"请求重试失败：{exc}", "#ff5555")

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

                    # Show resolved prompt (inline, preset, or global default)
                    try:
                        from src.provider.provider_config import provider_manager as _pm
                        resolved_prompt = _pm.get_provider_prompt()
                    except Exception:
                        resolved_prompt = active.settings.get("prompt")
                    if resolved_prompt:
                        prompt_normalized = " ".join(
                            line.strip() for line in str(resolved_prompt).splitlines() if line.strip()
                        )
                        max_len = 1000
                        prompt_to_show = (
                            prompt_normalized if len(prompt_normalized) <= max_len else prompt_normalized[: max_len - 3] + "..."
                        )
                        self.text_box_client.append(f"转录提示: {prompt_to_show}")
                    else:
                        self.text_box_client.append("转录提示: (none)")
        else:
            self.text_box_client.append("转录服务提供商: 未配置")

        self.text_box_client.append("================")


    def create_systray_icon(self):
        self.tray_icon = QSystemTrayIcon(self)
        try:
            self.tray_icon.setIcon(QIcon(str(client_icon_path())))
        except Exception:
            pass

        reload_providers_action = QAction("⚡ Reload Providers", self)
        explore_home_folder_action = QAction("📁 Open Home Folder With Explorer", self)
        vscode_home_folder_action = QAction("🤓 Open Home Folder With VSCode", self)

        show_action = QAction("🪟 Show", self)
        restart_client_action = QAction("🔄 Restart Client", self)
        quit_action = QAction("❌ Quit", self)

        reload_providers_action.triggered.connect(self.reload_providers)
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

        self.tray_menu.addSeparator()
        self.tray_menu.addAction(show_action)
        self.tray_menu.addAction(restart_client_action)
        self.tray_menu.addAction(quit_action)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.show()

        # Proactively warm up the tray menu to avoid first-use lag
        try:
            # Defer warm-up well past startup to avoid competing with initial work
            delay_ms = int(os.getenv("CW_TRAY_WARMUP_DELAY_MS", "15000"))
            if delay_ms < 0:
                delay_ms = 0
            QTimer.singleShot(delay_ms, self._warm_up_tray_menu)
        except Exception:
            pass

    def run_test_all_providers(self):
        """Launch availability test script and stream its output to the GUI."""
        try:
            exe = resolve_pythonw_client()
            if exe is None:
                self.text_box_client.append("无法启动测试：未找到可用的 Python 运行时。")
                return
            script = availability_test_script_path()
            if not script.exists():
                self.text_box_client.append(f"找不到测试脚本：{script}")
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

        Kept intentionally lightweight and deferred to avoid affecting startup.
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
        except Exception:
            # Never let warm-up impact the app
            pass

    def restart_client(self):
        # Important: run the restart helper with the console Python (python.exe),
        # not pythonw.exe. Otherwise the helper would be killed by its own taskkill.
        exe = resolve_console_python()
        try:
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            subprocess.Popen(
                [exe, str(restart_script_path())],
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
                self.populate_provider_combo()
                self.populate_model_combo()
                self.populate_prompt_combo()
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
        try:
            self.status_overlay.hide_all()
        except Exception:
            pass
        # Terminate core_client.py and any launcher-spawned child processes from this checkout.
        self._stop_core_client_processes()

        # Hide the system tray icon
        self.tray_icon.setVisible(False)

        # Quit the application
        QApplication.quit()

    def on_tray_icon_activated(self, reason):
        # Called when the system tray icon is activated
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.showNormal()  # Show the main window

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()  # Press ESC to hide main window

    def start_script(self):
        # Start core_client.py and redirect output to the client queue
        exe = resolve_pythonw_client()
        if exe is None:
            try:
                self.text_box_client.append("未找到可用的 Python 运行时。请确保已运行 'uv sync' 安装依赖。")
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
                # Support structured GUI messages emitted by src.gui_output.gui_print
                try:
                    if isinstance(line, str) and line.startswith("CW_GUI:"):
                        import json

                        payload = json.loads(line[len("CW_GUI:") :])
                        event = payload.get("event")
                        if event in {"status_overlay", "listening_overlay"}:
                            self._handle_status_overlay_event(payload)
                            continue
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

    def _handle_status_overlay_event(self, payload: dict) -> None:
        try:
            action = payload.get("action")
            state = payload.get("state")
            if action == "show" and state == "listening":
                self.status_overlay.show_listening()
            elif action == "show" and state in {"transcribing", "polishing"}:
                self.status_overlay.show_processing(str(state))
            elif action == "show":
                self.status_overlay.show_listening()
            elif action == "hide":
                self.status_overlay.hide_all()
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

    def _start_worker(self, script_rel_path: str, attr_name: str) -> None:
        exe = resolve_pythonw_client()
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
        try:
            self.status_overlay.hide_all()
        except Exception:
            pass
        # Stop existing workers
        self._stop_core_client_processes()

        # Core client last
        self._start_worker("core_client.py", "core_client_process")



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
        if hasattr(self, 'provider_combo'):
            widgets.append(self.provider_combo)
        if hasattr(self, 'modify_prompt_button'):
            widgets.append(self.modify_prompt_button)
        if hasattr(self, 'edit_polish_prompt_button'):
            widgets.append(self.edit_polish_prompt_button)
        if hasattr(self, 'edit_lexicon_button'):
            widgets.append(self.edit_lexicon_button)
        if hasattr(self, 'clear_history_button'):
            widgets.append(self.clear_history_button)
        if hasattr(self, 'retry_latest_button'):
            widgets.append(self.retry_latest_button)
        if hasattr(self, 'clear_screen_button'):
            widgets.append(self.clear_screen_button)
        if hasattr(self, 'model_combo'):
            widgets.append(self.model_combo)
        if hasattr(self, 'model_label'):
            widgets.append(self.model_label)
        if hasattr(self, 'prompt_combo'):
            widgets.append(self.prompt_combo)
        if hasattr(self, 'prompt_label'):
            widgets.append(self.prompt_label)

        for widget in widgets:
            # 检查字体大小是否已设置，如果没有设置，则使用一个默认值
            current_font = widget.font()
            if current_font.pointSizeF() < 9:
                current_font.setPointSizeF(9)  # 设置一个默认字体大小
            current_font.setPointSizeF(current_font.pointSizeF() * self.scale_factor)
            widget.setFont(current_font)


def start_client_gui(profile_options: StartupProfileOptions | None = None):
    if not acquire_single_instance(ROOT, "client_gui"):
        print("已有 CapsWriter GUI 实例正在运行，本次启动已退出。")
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
                delay = max(100, int((profile_options.duration_ms if profile_options else 5000)))
                QTimer.singleShot(delay, lambda: startup_profiler.stop("startup window"))
        except Exception:
            startup_profiler.stop("timer schedule failed")
        sys.exit(app.exec())
    finally:
        release_single_instance(ROOT, "client_gui")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="处理文件")
    parser.add_argument("files", nargs="*", type=Path, help="要处理的文件")
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
    profile_output = args.profile_output or (Path(profile_output_env) if profile_output_env else None)
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
