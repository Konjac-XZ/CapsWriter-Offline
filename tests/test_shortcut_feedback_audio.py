from __future__ import annotations

from dataclasses import dataclass, field

from src.keyboard import shortcut_handler


@dataclass
class FakeVolume:
    mute_values: list[int] = field(default_factory=list)

    def SetMute(self, value: int, _event_context) -> None:
        self.mute_values.append(value)


class FakeProcess:
    def __init__(self, name: str) -> None:
        self._name = name

    def name(self) -> str:
        return self._name


class FakeSession:
    def __init__(self, process_name: str) -> None:
        self.Process = FakeProcess(process_name)
        self.SimpleAudioVolume = FakeVolume()


def test_feedback_process_matching_is_case_insensitive() -> None:
    assert shortcut_handler._is_feedback_audio_process("ffplay.exe")
    assert shortcut_handler._is_feedback_audio_process("CapsWriter.WinUI.exe")
    assert shortcut_handler._is_feedback_audio_process("CAPSWRITER.WINUI.EXE")
    assert not shortcut_handler._is_feedback_audio_process("music.exe")
    assert not shortcut_handler._is_feedback_audio_process(None)


def test_recording_mute_preserves_feedback_audio_sessions(monkeypatch) -> None:
    native = FakeSession("CapsWriter.WinUI.exe")
    legacy = FakeSession("ffplay.exe")
    other = FakeSession("music.exe")
    monkeypatch.setattr(
        shortcut_handler.AudioUtilities,
        "GetAllSessions",
        lambda: [native, legacy, other],
    )

    shortcut_handler.mute_all_sessions()
    shortcut_handler.unmute_all_sessions()

    assert native.SimpleAudioVolume.mute_values == []
    assert legacy.SimpleAudioVolume.mute_values == []
    assert other.SimpleAudioVolume.mute_values == [1, 0]
