from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtGui import QColor, QTextCursor, QTextDocument
from PySide6.QtWidgets import QApplication, QPlainTextEdit

from start_client_gui import (
    GUI,
    _append_log_document_entries,
    _configure_log_document_retention,
)


def test_log_widget_uses_plain_text_engine_without_undo_history() -> None:
    app = QApplication.instance() or QApplication([])
    owner = SimpleNamespace()

    GUI.create_text_box(owner)

    assert isinstance(owner.text_box_client, QPlainTextEdit)
    assert owner.text_box_client.isUndoRedoEnabled() is False
    assert owner.text_box_client.document().maximumBlockCount() == 500
    owner.text_box_client.deleteLater()
    app.processEvents()


def test_log_document_discards_oldest_blocks_at_limit() -> None:
    document = QTextDocument()
    _configure_log_document_retention(document, max_blocks=3)
    cursor = QTextCursor(document)

    for line in ("first", "second", "third", "fourth", "fifth"):
        cursor.movePosition(QTextCursor.MoveOperation.End)
        if not document.isEmpty():
            cursor.insertBlock()
        cursor.insertText(line)

    assert document.maximumBlockCount() == 3
    assert document.blockCount() == 3
    assert document.toPlainText() == "third\nfourth\nfifth"


def test_log_document_batch_preserves_per_entry_colors() -> None:
    document = QTextDocument()

    _append_log_document_entries(
        document,
        [
            ("ordinary", QColor("#112233")),
            ("warning", QColor("#ff8800")),
            ("success", QColor("#008000")),
        ],
    )

    assert document.toPlainText() == "ordinary\nwarning\nsuccess"
    expected = ["#112233", "#ff8800", "#008000"]
    block = document.begin()
    for color in expected:
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.MoveOperation.NextCharacter)
        assert cursor.charFormat().foreground().color().name() == color
        block = block.next()
