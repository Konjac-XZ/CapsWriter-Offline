import sys
from types import SimpleNamespace

from src.keyboard import play_music


def test_candidate_roots_include_pyinstaller_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(play_music.sys, "_MEIPASS", str(tmp_path), raising=False)

    assert tmp_path in play_music._candidate_project_roots()


def test_play_completion_sound_uses_native_async_wav(monkeypatch, tmp_path):
    sound_file = tmp_path / "bubble.wav"
    sound_file.write_bytes(b"test")
    calls = []
    fake_winsound = SimpleNamespace(
        SND_FILENAME=1,
        SND_ASYNC=2,
        SND_NODEFAULT=4,
        PlaySound=lambda path, flags: calls.append((path, flags)),
    )

    monkeypatch.setattr(play_music, "_resolve_audio_file", lambda path: sound_file)
    monkeypatch.setattr(play_music.sys, "platform", "win32")
    monkeypatch.setitem(sys.modules, "winsound", fake_winsound)

    play_music.play_completion_sound()

    assert calls == [(str(sound_file), 1 | 2 | 4)]


def test_native_gui_feedback_emits_cue_without_starting_player(monkeypatch):
    events = []
    monkeypatch.setenv("CAPSWRITER_GUI_PROTOCOL", "1")
    monkeypatch.setattr(
        "src.infra.gui_output.gui_event",
        lambda event, **payload: events.append((event, payload)),
    )
    monkeypatch.setattr(
        play_music,
        "play_music",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("ffplay path used")
        ),
    )

    play_music.play_feedback_sound(
        "start", play_music.Path("assets/SpeechOn.mp3"), "100"
    )
    play_music.play_completion_sound()

    assert events == [
        ("audio_cue", {"cue": "start"}),
        ("audio_cue", {"cue": "success"}),
    ]


def test_legacy_gui_feedback_keeps_existing_player(monkeypatch, tmp_path):
    calls = []
    sound_file = tmp_path / "SpeechOff.mp3"
    monkeypatch.delenv("CAPSWRITER_GUI_PROTOCOL", raising=False)
    monkeypatch.setattr(
        play_music,
        "play_music",
        lambda path, volume: calls.append((path, volume)),
    )

    play_music.play_feedback_sound("stop", sound_file, "75")

    assert calls == [(sound_file, "75")]
