from __future__ import annotations

import json
from pathlib import Path

from src.gui import lexicon_editor_client


class _ProcessStub:
    def __init__(self, return_code: int | None = None) -> None:
        self.return_code = return_code

    def poll(self) -> int | None:
        return self.return_code


def test_show_starts_process_and_writes_command(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        lexicon_editor_client.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(tmp_path),
    )
    popen_calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_popen(command: list[str], **kwargs: object) -> _ProcessStub:
        popen_calls.append((command, kwargs))
        return _ProcessStub()

    monkeypatch.setattr(lexicon_editor_client.subprocess, "Popen", fake_popen)
    client = lexicon_editor_client.LexiconEditorProcessClient(tmp_path, "pythonw.exe")

    assert client.show() is True
    assert popen_calls[0][0][:4] == [
        "pythonw.exe",
        "-m",
        "src.gui.lexicon_editor_process",
        str(tmp_path),
    ]
    assert json.loads((tmp_path / "command.json").read_text(encoding="utf-8")) == {
        "serial": 1,
        "command": "show",
    }


def test_poll_event_returns_each_serial_once(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        lexicon_editor_client.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(tmp_path),
    )
    client = lexicon_editor_client.LexiconEditorProcessClient(tmp_path, "pythonw.exe")
    event_path = tmp_path / "event.json"
    event_path.write_text('{"serial": 0, "event": "ready"}', encoding="utf-8")

    assert client.poll_event() == {"serial": 0, "event": "ready"}
    assert client.poll_event() is None

    event_path.write_text('{"serial": 1, "event": "saved"}', encoding="utf-8")
    assert client.poll_event() == {"serial": 1, "event": "saved"}


def test_client_becomes_ready_only_after_editor_is_opened(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        lexicon_editor_client.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(tmp_path),
    )
    client = lexicon_editor_client.LexiconEditorProcessClient(tmp_path, "pythonw.exe")
    process = _ProcessStub()
    client._process = process  # ty: ignore[invalid-assignment]

    assert client.is_ready() is False

    event_path = tmp_path / "event.json"
    event_path.write_text('{"serial": 1, "event": "opened"}', encoding="utf-8")
    assert client.poll_event() == {"serial": 1, "event": "opened"}
    assert client.is_ready() is True

    process.return_code = 1
    assert client.is_ready() is False


def test_show_reuses_running_process(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        lexicon_editor_client.tempfile,
        "mkdtemp",
        lambda **_kwargs: str(tmp_path),
    )
    client = lexicon_editor_client.LexiconEditorProcessClient(tmp_path, "pythonw.exe")
    process = _ProcessStub()
    client._process = process  # ty: ignore[invalid-assignment]

    assert client.show() is True
    assert client.process is process
    assert (
        json.loads((tmp_path / "command.json").read_text(encoding="utf-8"))["serial"]
        == 1
    )
