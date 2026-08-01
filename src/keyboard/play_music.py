# def generate_tone(freq, dur, sr=16000):
#     import numpy as np

#     t = np.linspace(0, dur, int(sr * dur), False)
#     return np.sin(2 * np.pi * freq * t)


# def generate_wav(file_path, tones):
#     import wave

#     import numpy as np

#     # 生成音调并合并
#     combined_audio = np.hstack(tones)

#     # 将音频保存为 WAV 文件
#     with wave.open(str(file_path), "w") as wf:
#         wf.setnchannels(1)  # 单声道
#         wf.setsampwidth(2)  # 2 字节（16 位）
#         wf.setframerate(16000)  # 采样率
#         # 将音频数据转换为 16 位整数格式
#         audio_int16 = (combined_audio * 32767).astype(np.int16)
#         wf.writeframes(audio_int16.tobytes())


# def play_wav(file_path, volume=0.5):
#     """
#     播放 WAV 文件
#     :param file_path: WAV 文件路径
#     :param volume: 音量增益
#     """
#     import wave

#     import numpy as np
#     import sounddevice as sd

#     try:
#         with wave.open(str(file_path), "r") as wf:
#             sr = wf.getframerate()
#             frames = wf.readframes(wf.getnframes())
#             # 将字节数据转换为 numpy 数组
#             audio_data = (
#                 np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32767
#             )
#             # 调整音量
#             audio_data = audio_data * volume
#             # 确保音频数据在合法范围内 [0, 1.0]
#             audio_data = np.clip(audio_data, 0, 1.0)
#         sd.play(audio_data, sr)
#         sd.wait()
#     except sd.PortAudioError as e:
#         console.print(f"音频播放失败: {e}")

import shutil
import sys
from pathlib import Path

from src.infra.cosmic import console


def _candidate_project_roots() -> list[Path]:
    roots = []
    cwd = Path.cwd()
    roots.append(cwd)
    roots.append(Path(__file__).resolve().parents[2])
    if bundle_root := getattr(sys, "_MEIPASS", None):
        roots.append(Path(bundle_root))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    return list(dict.fromkeys(roots))


def _resolve_audio_file(file_path: Path) -> Path | None:
    path = Path(file_path)

    if path.is_absolute() and path.exists():
        return path

    name = path.name.lower()
    legacy_map = {
        "speechon.mp3": "start.mp3",
        "speechoff.mp3": "stop.mp3",
    }

    relative_candidates: list[Path] = [path]

    if path.parent == Path("."):
        relative_candidates.append(Path("assets") / path.name)

    if name in legacy_map:
        relative_candidates.append(Path("assets") / legacy_map[name])

    for root in _candidate_project_roots():
        for relative in relative_candidates:
            candidate = root / relative
            if candidate.exists():
                return candidate
        dist_assets = root / "dist" / "start_client_gui" / "_internal" / "assets"
        if name in legacy_map:
            candidate = dist_assets / legacy_map[name]
            if candidate.exists():
                return candidate
        candidate = dist_assets / path.name
        if candidate.exists():
            return candidate

    return None


def _resolve_ffplay_exe() -> str | None:
    if ffplay := shutil.which("ffplay"):
        return ffplay

    for root in _candidate_project_roots():
        for candidate in (root / "ffplay.exe", root / "bin" / "ffplay.exe"):
            if candidate.exists():
                return str(candidate)

    return None


def _fallback_beep():
    try:
        import winsound

        winsound.MessageBeep(winsound.MB_ICONASTERISK)
    except Exception:
        pass


def play_completion_sound() -> None:
    """Play the bundled completion sound without blocking result delivery."""
    sound_file = _resolve_audio_file(Path("bubble.wav"))
    if sound_file is None:
        console.print("上屏提示音文件不存在: bubble.wav")
        return

    if sys.platform == "win32":
        try:
            import winsound

            flags = winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT
            winsound.PlaySound(str(sound_file), flags)
            return
        except Exception as exc:
            console.print(f"播放上屏提示音失败: {exc}")

    play_music(sound_file, "100")


def play_music(file_path: Path, volume_level: str = "50"):
    """
    调用ffplay播放WAV文件，并指定音量，且不显示控制台窗口，并在播放完毕后自动退出。
    """
    import subprocess
    import threading

    resolved_file = _resolve_audio_file(file_path)
    ffplay_exe = _resolve_ffplay_exe()

    if not resolved_file:
        console.print(f"提示音文件不存在: {file_path}，改为系统提示音。")
        _fallback_beep()
        return

    if not ffplay_exe:
        console.print("未找到 ffplay.exe，改为系统提示音。")
        _fallback_beep()
        return

    command = [
        ffplay_exe,
        "-nodisp",
        "-v",
        "quiet",
        "-volume",
        str(volume_level),
        "-autoexit",
        str(resolved_file),
    ]
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = subprocess.SW_HIDE
    try:
        # Start the ffplay process in a separate thread to avoid blocking the main script
        # console.print(f"播放声音 {str(file_path)}")
        threading.Thread(
            target=lambda: subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_NO_WINDOW,
                startupinfo=startupinfo,
            )
        ).start()
    except FileNotFoundError:
        console.print("ffplay.exe 未找到，改为系统提示音。")
        _fallback_beep()
    except Exception as e:
        console.print(f"发生错误: {e}")
        _fallback_beep()


if __name__ == "__main__":
    from pathlib import Path
    from time import sleep

    # wav_file = Path.cwd() / "assets" / "start.wav"
    # tones = [generate_tone(f, d) for f, d in [(220, 0.2), (330, 0.4)]]
    # generate_wav(wav_file, tones)
    # play_wav(wav_file)
    # wav_file = Path.cwd() / "assets" / "stop.wav"
    # tones = [generate_tone(f, d) for f, d in [(330, 0.1), (220, 0.2)]]
    # generate_wav(wav_file, tones)
    # play_wav(wav_file)

    mp3_file = Path.cwd() / "assets" / "start.mp3"
    play_music(mp3_file)
    sleep(1)
    mp3_file = Path.cwd() / "assets" / "stop.mp3"
    play_music(mp3_file)
