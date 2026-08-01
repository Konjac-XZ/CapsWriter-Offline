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
