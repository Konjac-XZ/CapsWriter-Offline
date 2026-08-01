from __future__ import annotations

from types import SimpleNamespace

from PySide6.QtWidgets import QDialog

from src.gui import lexicon_editor


class _EditorStub:
    def __init__(self) -> None:
        self.values: list[str] = []

    def set_text(self, value: str) -> None:
        self.values.append(value)


class _DialogStub:
    def __init__(self, result: QDialog.DialogCode) -> None:
        self.result = result
        self.prepared_texts: list[str] = []
        self.saved_value: str | None = None

    def prepare_for_open(self, initial_text: str) -> None:
        self.prepared_texts.append(initial_text)

    def exec(self) -> QDialog.DialogCode:
        return self.result

    def saved_text(self) -> str | None:
        return self.saved_value


def test_prepare_for_open_resets_saved_state_and_replaces_editor_text() -> None:
    editor = _EditorStub()
    dialog = SimpleNamespace(_saved_text="previous save", editor_api=editor)

    lexicon_editor.LexiconEditDialog.prepare_for_open(
        dialog,  # ty: ignore[invalid-argument-type]
        "words:\n- refreshed\n",
    )

    assert dialog._saved_text is None
    assert editor.values == ["words:\n- refreshed\n"]


def test_reusable_dialog_refreshes_from_disk_before_every_open(monkeypatch) -> None:
    disk_texts = iter(("words:\n- first\n", "words:\n- second\n"))
    monkeypatch.setattr(lexicon_editor, "read_lexicon_text", lambda: next(disk_texts))
    dialog = _DialogStub(QDialog.DialogCode.Rejected)

    assert lexicon_editor.open_lexicon_editor(
        dialog=dialog  # ty: ignore[invalid-argument-type]
    ) == (False, None)
    assert lexicon_editor.open_lexicon_editor(
        dialog=dialog  # ty: ignore[invalid-argument-type]
    ) == (False, None)

    assert dialog.prepared_texts == ["words:\n- first\n", "words:\n- second\n"]


def test_reusable_dialog_returns_the_latest_saved_text(monkeypatch) -> None:
    monkeypatch.setattr(lexicon_editor, "read_lexicon_text", lambda: "words: []\n")
    dialog = _DialogStub(QDialog.DialogCode.Accepted)
    dialog.saved_value = "words:\n- saved\n"

    assert lexicon_editor.open_lexicon_editor(
        dialog=dialog  # ty: ignore[invalid-argument-type]
    ) == (
        True,
        "words:\n- saved\n",
    )
