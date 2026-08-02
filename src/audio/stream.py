import asyncio
import sys
import threading
import time
from contextlib import contextmanager
from typing import Any, cast

import numpy as np
import sounddevice as sd

from src.audio.level_publisher import update_latest_level
from src.infra.cosmic import Cosmic, console
from src.infra.config import ClientConfig as Config

_DEBUG_SLOW_MS = 250.0
_debug_stream_ops = 0


def _debug_enabled(force: bool = False) -> bool:
    return force


def _debug_log(message: str, *, force: bool = False) -> None:
    if not _debug_enabled(force):
        return
    try:
        console.print(f"[timing][stream] {message}", style="dim")
    except Exception:
        pass


@contextmanager
def _timed_step(name: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _debug_log(
            f"{name} {elapsed_ms:.1f}ms",
            force=elapsed_ms >= _DEBUG_SLOW_MS,
        )


def record_callback(
    indata: np.ndarray, frames: int, time_info, status: sd.CallbackFlags
) -> None:
    if not Cosmic.on:
        return
    if Cosmic.loop is None:
        return
    now = time.time()
    update_latest_level(indata)
    asyncio.run_coroutine_threadsafe(
        Cosmic.queue_in.put(
            {
                "type": "data",
                "time": now,
                "data": indata.copy(),
            },
        ),
        Cosmic.loop,
    )


def stream_close(signum, frame):
    if Cosmic.stream is not None:
        Cosmic.stream.close()


def stream_reopen():
    global _debug_stream_ops
    _debug_stream_ops += 1
    total_start = time.perf_counter()
    if not threading.main_thread().is_alive():
        return
    console.print("\n正在聆听……", style="green")

    # 关闭旧流
    if Cosmic.stream is not None:
        with _timed_step("reopen:old_stream_close"):
            Cosmic.stream.close()

    # 重载 PortAudio，更新设备列表
    with _timed_step("reopen:portaudio_terminate"):
        sd._terminate()
    with _timed_step("reopen:portaudio_dlclose"):
        sd._ffi.dlclose(sd._lib)
    libname = sd._libname
    if libname is not None:
        with _timed_step("reopen:portaudio_dlopen"):
            sd._lib = sd._ffi.dlopen(libname)
    with _timed_step("reopen:portaudio_initialize"):
        sd._initialize()

    # 打开新流
    with _timed_step("reopen:sleep_before_open"):
        time.sleep(0.1)
    with _timed_step("reopen:stream_open"):
        Cosmic.stream = stream_open()
    total_ms = (time.perf_counter() - total_start) * 1000.0
    _debug_log(
        f"reopen#{_debug_stream_ops} end total={total_ms:.1f}ms",
        force=total_ms >= _DEBUG_SLOW_MS,
    )


def stream_open():
    total_start = time.perf_counter()
    # 显示录音所用的音频设备
    channels = 1
    try:
        with _timed_step("open:query_devices"):
            device = cast(dict[str, Any], sd.query_devices(kind="input"))
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
        with _timed_step("open:InputStream_ctor"):
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
        with _timed_step("open:InputStream_ctor"):
            stream = sd.InputStream(
                samplerate=48000,
                blocksize=int(0.05 * 48000),  # 0.05 seconds
                device=None,
                dtype="float32",
                channels=channels,
                callback=record_callback,
                finished_callback=stream_reopen,
            )
        with _timed_step("open:stream_start"):
            stream.start()

    total_ms = (time.perf_counter() - total_start) * 1000.0
    _debug_log(f"open end total={total_ms:.1f}ms", force=total_ms >= _DEBUG_SLOW_MS)
    return stream
