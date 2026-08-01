from __future__ import annotations

from typing import Any, Protocol, cast

import yaml
from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QMessageBox,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from src.infra.user_lexicon import _lexicon_path


DEFAULT_LEXICON_TEXT = "words: []\n"


class LexiconYamlDumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False) -> Any:
        return super().increase_indent(flow, False)


class TextEditorWidget(Protocol):
    def set_text(self, value: str) -> None: ...

    def get_text(self) -> str: ...


class PlainYamlEditor(QPlainTextEdit):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setTabChangesFocus(False)

    def set_text(self, value: str) -> None:
        self.setPlainText(value)

    def get_text(self) -> str:
        return self.toPlainText()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter} and self._continue_yaml_list():
            return
        super().keyPressEvent(event)

    def _continue_yaml_list(self) -> bool:
        cursor = self.textCursor()
        block_text = cursor.block().text()
        before_cursor = block_text[: cursor.positionInBlock()]
        indent = before_cursor[: len(before_cursor) - len(before_cursor.lstrip(" "))]
        content = before_cursor[len(indent) :]
        if not content.startswith("- "):
            return False

        cursor.insertText("\n")
        if content.strip() != "-":
            cursor.insertText(f"{indent}- ")
        self.setTextCursor(cursor)
        return True


class _MonacoKeyInterceptor(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_S and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
                dialog = self._find_dialog_ancestor(watched)
                if dialog is not None:
                    dialog._handle_accept()
                    return True
        return super().eventFilter(watched, event)

    def _find_dialog_ancestor(self, obj: QObject | None) -> LexiconEditDialog | None:
        current = obj
        while current is not None:
            if isinstance(current, LexiconEditDialog):
                return current
            current = current.parent()
        return None


class MonacoYamlEditor(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._monaco = self._create_monaco_widget()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._monaco)

    def _create_monaco_widget(self) -> QWidget:
        # Local import keeps qtmonaco off the cold-start path.
        from qtmonaco import Monaco

        editor = Monaco(self)
        editor.installEventFilter(_MonacoKeyInterceptor(editor))
        editor.initialized.connect(lambda: self._configure_editor(editor))
        editor.set_language("yaml")
        editor.set_theme("vs")
        return editor

    def _configure_editor(self, editor: QWidget) -> None:
        page = getattr(editor, "page", None)
        if not callable(page):
            return
        web_page = cast(Any, page())
        if web_page is None:
            return
        web_page.runJavaScript(
            r"""
            (() => {
              const state = window.qtmonaco;
              if (!state || !state.editor || !state.monaco) {
                return;
              }
              const editor = state.editor;
              const monaco = state.monaco;
                            monaco.editor.setTheme('vs');
              editor.updateOptions({
                autoIndent: 'keep',
                autoClosingBrackets: 'languageDefined',
                detectIndentation: false,
                formatOnPaste: false,
                formatOnType: false,
                insertSpaces: true,
                quickSuggestions: true,
                scrollBeyondLastLine: false,
                tabSize: 2,
              });
              if (window.__capswriterYamlListContinuation) {
                return;
              }
              window.__capswriterYamlListContinuation = true;
              editor.addCommand(monaco.KeyCode.Enter, () => {
                const model = editor.getModel();
                const position = editor.getPosition();
                if (!model || !position) {
                  editor.trigger('keyboard', 'type', { text: '\n' });
                  return;
                }
                const lineText = model.getLineContent(position.lineNumber);
                const prefix = lineText.slice(0, Math.max(position.column - 1, 0));
                const match = prefix.match(/^(\s*)-\s+(.*)$/);
                if (!match) {
                  editor.trigger('keyboard', 'type', { text: '\n' });
                  return;
                }
                const indent = match[1] || '';
                const payload = match[2].trim().length > 0 ? `\n${indent}- ` : '\n';
                const selection = editor.getSelection();
                const range = selection || new monaco.Range(
                  position.lineNumber,
                  position.column,
                  position.lineNumber,
                  position.column
                );
                editor.executeEdits('capswriter-yaml-list-continuation', [{
                  range,
                  text: payload,
                  forceMoveMarkers: true,
                }]);
                const lines = payload.split('\n');
                const nextLine = range.startLineNumber + lines.length - 1;
                const nextColumn = lines.length === 1
                  ? range.startColumn + lines[0].length
                  : lines[lines.length - 1].length + 1;
                editor.setPosition({ lineNumber: nextLine, column: nextColumn });
              });
            })();
            """
        )

    def set_text(self, value: str) -> None:
        monaco = self._monaco
        set_text = getattr(monaco, "set_text", None)
        if callable(set_text):
            set_text(value, language="yaml")

    def get_text(self) -> str:
        monaco = self._monaco
        get_text = getattr(monaco, "get_text", None)
        if callable(get_text):
            result = get_text()
            return result if isinstance(result, str) else str(result)
        return ""


def _create_editor(parent: QWidget) -> TextEditorWidget:
    try:
        return MonacoYamlEditor(parent)
    except Exception:
        return PlainYamlEditor(parent)


def read_lexicon_text() -> str:
    lexicon_path = _lexicon_path()
    try:
        with lexicon_path.open("r", encoding="utf-8", newline="") as handle:
            return handle.read()
    except FileNotFoundError:
        return DEFAULT_LEXICON_TEXT


def write_lexicon_text(text: str) -> None:
    lexicon_path = _lexicon_path()
    lexicon_path.parent.mkdir(parents=True, exist_ok=True)
    with lexicon_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)


def normalize_lexicon_text(text: str) -> str:
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError("用户词库必须是包含 words 数组的 YAML 对象")

    words = data.get("words") or []
    if not isinstance(words, list):
        raise ValueError("用户词库的 words 必须是 YAML 数组")

    normalized_words: list[str] = []
    for word in words:
        if isinstance(word, (dict, list, tuple, set)):
            raise ValueError("用户词库的 words 只能包含扁平文本项")
        normalized_words.append(str(word))

    if not normalized_words:
        return DEFAULT_LEXICON_TEXT
    return yaml.dump(
        {"words": normalized_words},
        Dumper=LexiconYamlDumper,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )


class LexiconEditDialog(QDialog):
    """Dialog for editing the user lexicon YAML file."""

    def __init__(self, parent: QWidget | None = None, *, initial_text: str = "") -> None:
        super().__init__(parent)
        self._saved_text: str | None = None
        self.setWindowTitle("编辑用户词库")
        self.resize(760, 520)

        layout = QVBoxLayout(self)

        self.editor_api = _create_editor(self)
        self.editor_api.set_text(initial_text)
        self.editor_widget = cast(QWidget, self.editor_api)
        layout.addWidget(self.editor_widget)
        layout.setStretchFactor(self.editor_widget, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self._handle_accept)
        buttons.rejected.connect(self.reject)
        try:
            buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        except Exception:
            pass
        layout.addWidget(buttons)

    def saved_text(self) -> str | None:
        return self._saved_text

    def prepare_for_open(self, initial_text: str) -> None:
        """Reset transient state and refresh the editor from the on-disk text."""
        self._saved_text = None
        self.editor_api.set_text(initial_text)

    def _handle_accept(self) -> None:
        raw_text = self.editor_api.get_text()
        try:
            normalized_text = normalize_lexicon_text(raw_text)
        except Exception as exc:
            QMessageBox.critical(self, "YAML 格式错误", f"用户词库 YAML 格式错误，未保存：\n{exc}")
            return

        try:
            write_lexicon_text(normalized_text)
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", f"保存用户词库失败：\n{exc}")
            return

        self._saved_text = normalized_text
        self.accept()


def open_lexicon_editor(
    parent: QWidget | None = None,
    *,
    dialog: LexiconEditDialog | None = None,
) -> tuple[bool, str | None]:
    """Open a fresh or reusable lexicon dialog with the latest on-disk text."""
    initial_text = read_lexicon_text()
    if dialog is None:
        dialog = LexiconEditDialog(parent, initial_text=initial_text)
    else:
        dialog.prepare_for_open(initial_text)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False, None
    return True, dialog.saved_text()
