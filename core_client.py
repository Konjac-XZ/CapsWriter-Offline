# coding: utf-8
import asyncio
import contextlib
import os
import signal
import sys
from platform import system

import colorama

from src.infra.env_loader import load_dotenv_files
from src.infra.cosmic import Cosmic, console

try:
    load_dotenv_files()
except Exception:
    pass

from src.pipeline.recv_result import recv_result
from src.keyboard.shortcut_handler import bond_shortcut
from src.audio.retry_cache import read_retry_request
from src.audio.send_audio import retry_latest_audio
from src.audio.stream import stream_close, stream_open
from src.polish.vision_context import start_vision_context_service, stop_vision_context_service
from src.system.empty_working_set import empty_current_working_set

Cosmic.transcribe_subtitles = bool(sys.argv[1:])

# 确保根目录位置正确，用相对路径加载模型
BASE_DIR = os.getcwd()
os.chdir(BASE_DIR)
# BASE_DIR = os.path.dirname(__file__); os.chdir(BASE_DIR)

# 确保终端能使用 ANSI 控制字符
colorama.init()

# MacOS 的权限设置
if system() == "Darwin" and not sys.argv[1:]:
    getuid = getattr(os, "getuid", None)
    if callable(getuid) and getuid() != 0:
        print("在 MacOS 上需要以管理员启动客户端才能监听键盘活动，请 sudo 启动")
        input("按回车退出")
        sys.exit()
    else:
        os.umask(0o000)


async def watch_retry_requests():
    initial_payload = read_retry_request()
    last_request_id = (
        initial_payload.get("request_id") if isinstance(initial_payload, dict) else None
    )
    retry_task = None

    while True:
        try:
            payload = read_retry_request()
            request_id = payload.get("request_id") if isinstance(payload, dict) else None

            if request_id is not None and request_id != last_request_id:
                last_request_id = request_id

                if Cosmic.on or getattr(Cosmic, "transcribe_busy", False):
                    console.print("当前正在识别，稍后再重试。", style="bright_yellow")
                elif retry_task is not None and not retry_task.done():
                    console.print("当前已有重试请求正在进行。", style="bright_yellow")
                else:
                    retry_task = asyncio.create_task(retry_latest_audio())

            await asyncio.sleep(0.4)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            console.print(f"监听重试请求失败：{exc}", style="bright_red")
            await asyncio.sleep(1.0)


async def main_mic():
    Cosmic.loop = asyncio.get_event_loop()
    Cosmic.queue_in = asyncio.Queue()
    Cosmic.queue_out = asyncio.Queue()
    vision_task = None
    retry_watcher_task = None

    # 打开音频流
    Cosmic.stream = stream_open()

    # Ctrl-C 关闭音频流，触发自动重启
    signal.signal(signal.SIGINT, stream_close)

    # 绑定按键
    bond_shortcut()

    # 清空物理内存工作集
    if system() == "Windows":
        empty_current_working_set()

    vision_task = start_vision_context_service()
    retry_watcher_task = asyncio.create_task(watch_retry_requests())

    try:
        while True:
            await recv_result()
    finally:
        if retry_watcher_task is not None:
            retry_watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await retry_watcher_task
        if vision_task is not None:
            with contextlib.suppress(Exception):
                await stop_vision_context_service(vision_task)


def init_mic():
    try:
        asyncio.run(main_mic())
    except KeyboardInterrupt:
        console.print("再见！")
    finally:
        print("...")


if __name__ == "__main__":
    init_mic()
