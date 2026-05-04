from __future__ import annotations

from PySide6.QtWidgets import QDialog, QDialogButtonBox, QPlainTextEdit, QVBoxLayout, QWidget


class PromptEditDialog(QDialog):
    """Simple dialog for editing prompt text."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        initial_text: str = "",
        window_title: str = "编辑提示词",
    ):
        super().__init__(parent)
        self.setWindowTitle(window_title)
        self.resize(420, 320)

        layout = QVBoxLayout(self)
        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setPlainText(initial_text)
        layout.addWidget(self.text_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel,
            parent=self,
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        try:
            buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
            buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        except Exception:
            pass
        layout.addWidget(buttons)

    def prompt_text(self) -> str:
        return self.text_edit.toPlainText()
