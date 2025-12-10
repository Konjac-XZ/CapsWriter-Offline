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

from pathlib import Path

from util.client_cosmic import console


def play_music(file_path: Path, volume_level: str = "50"):
    """
    调用ffplay播放WAV文件，并指定音量，且不显示控制台窗口，并在播放完毕后自动退出。
    """
    import subprocess
    import threading

    command = ["ffplay", "-volume", volume_level, "-autoexit", str(file_path)]
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
        console.print("ffplay.exe未找到，请确保它在PATH中或提供完整路径。")
    except Exception as e:
        console.print(f"发生错误: {e}")


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
