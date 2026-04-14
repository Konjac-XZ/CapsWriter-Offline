import shutil
import tempfile
import time
import wave
import os
from os import makedirs
from pathlib import Path
from subprocess import CREATE_NO_WINDOW, DEVNULL, PIPE, Popen
from typing import Tuple, Union
from wave import Wave_write

from src.infra.config import ClientConfig as Config


def create_file(
    channels: int, time_start: float
) -> Tuple[Path, Union[Popen, Wave_write]]:
    time_year = time.strftime("%Y", time.localtime(time_start))
    time_month = time.strftime("%m", time.localtime(time_start))
    time_ymdhms = time.strftime("%Y%m%d-%H%M%S", time.localtime(time_start))

    folder_path = Path() / time_year / time_month / "assets"
    makedirs(folder_path, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=f"({time_ymdhms})", dir=folder_path)
    os.close(fd)
    file_path = Path(tmp_path)

    if shutil.which("ffmpeg") and Config.reduce_audio_files:
        # 用户已安装 ffmpeg，且设置使用减小音频文件，则输出到 mp3 文件
        file_path = file_path.with_suffix(".mp3")
        # 构造ffmpeg命令行
        ffmpeg_command = [
            "ffmpeg",
            "-y",
            "-f",
            "f32le",
            "-ar",
            "48000",
            "-ac",
            f"{channels}",
            "-i",
            "-",
            "-af",
            "silenceremove=start_periods=1:start_duration=0.1:start_threshold=-55dB:detection=peak",
            "-b:a",
            "192k",
            file_path,
        ]
        # 执行ffmpeg命令行，得到 Popen
        file = Popen(
            ffmpeg_command,
            creationflags=CREATE_NO_WINDOW,
            stdin=PIPE,
            stdout=DEVNULL,
            stderr=DEVNULL,
        )
    else:  # 用户未安装 ffmpeg，则输出为 wav 格式
        file_path = file_path.with_suffix(".wav")
        file = wave.open(str(file_path), "w")
        file.setnchannels(channels)
        file.setsampwidth(2)
        file.setframerate(48000)
    return file_path, file
