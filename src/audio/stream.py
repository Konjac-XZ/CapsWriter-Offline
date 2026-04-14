import asyncio
import sys
import threading
import time

import numpy as np
import sounddevice as sd

from src.infra.cosmic import Cosmic, console
from src.infra.config import ClientConfig as Config


def record_callback(
    indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags
) -> None:
    if not Cosmic.on:
        return
    if Cosmic.loop is None:
        return
    asyncio.run_coroutine_threadsafe(
        Cosmic.queue_in.put(
            {
                "type": "data",
                "time": time.time(),
                "data": indata.copy(),
            },
        ),
        Cosmic.loop,
    )


def stream_close(signum, frame):
    if Cosmic.stream is not None:
        Cosmic.stream.close()


def stream_reopen():
    if not threading.main_thread().is_alive():
        return
    console.print("\n正在聆听……", style="green")

    # 关闭旧流
    if Cosmic.stream is not None:
        Cosmic.stream.close()

    # 重载 PortAudio，更新设备列表
    sd._terminate()
    sd._ffi.dlclose(sd._lib)
    sd._lib = sd._ffi.dlopen(sd._libname)
    sd._initialize()

    # 打开新流
    time.sleep(0.1)
    Cosmic.stream = stream_open()


def stream_open():
    # 显示录音所用的音频设备
    channels = 1
    try:
        device = sd.query_devices(kind="input")
        device_name = device["name"]
        channels = min(2, device["max_input_channels"])
        # If device name doesn't include 'USB', warn the user once per run.
        try:
            if (
                not Cosmic.usb_warning_shown
                and isinstance(device_name, str)
                and "usb" not in device_name.lower()
            ):
                console.print(
                    "警告：检测到的麦克风设备名称不包含 'USB'。",
                    style="orange",
                )
                Cosmic.usb_warning_shown = True
        except Exception:
            # Be conservative: don't crash on unexpected device name types
            pass
    except UnicodeDecodeError:
        console.print(
            "由于编码问题，暂时无法获得麦克风设备名字", end="\n\n", style="bright_red"
        )
    except sd.PortAudioError:
        console.print("没有找到麦克风设备", end="\n\n", style="bright_red")
        input("按回车键退出")
        sys.exit()

    if Config.only_enable_microphones_when_pressed_record_shortcut:
        stream = sd.InputStream(
            samplerate=48000,
            blocksize=int(0.05 * 48000),  # 0.05 seconds
            device=None,
            dtype="float32",
            channels=channels,
            callback=record_callback,
            # finished_callback=stream_reopen,
        )  # stream.start()
    else:
        stream = sd.InputStream(
            samplerate=48000,
            blocksize=int(0.05 * 48000),  # 0.05 seconds
            device=None,
            dtype="float32",
            channels=channels,
            callback=record_callback,
            finished_callback=stream_reopen,
        )
        stream.start()

    return stream
