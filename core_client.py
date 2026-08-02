# coding: utf-8
import asyncio
import contextlib
import os
import signal
import sys
from pathlib import Path
from platform import system

import colorama

from src.infra.env_loader import load_dotenv_files
from src.infra.cosmic import Cosmic, console
from src.infra.runtime_logging import configure_runtime_logging

configure_runtime_logging("core_client")

try:
    load_dotenv_files()
except Exception:
    pass

from src.pipeline.recv_result import recv_result
from src.keyboard.shortcut_handler import abandon_current_task, bond_shortcut
from src.audio.control_requests import (
    claim_abandon_request,
    claim_clear_history_request,
    read_abandon_request,
    read_clear_history_request,
)
from src.audio.retry_cache import claim_retry_request, read_retry_request
from src.audio.send_audio import retry_latest_audio
from src.audio.level_publisher import publish_overlay_levels
from src.audio.stream import stream_close, stream_open
from src.polish.llm_polish import clear_finalized_history
from src.polish.vision_context import start_vision_context_service, stop_vision_context_service
from src.system.empty_working_set import empty_current_working_set
from src.system.process_cleanup import terminate_python_script_processes
from src.system.startup_replacement import prepare_replacement_startup, release_startup_slot
from src.tsf_ipc import get_tsf_speech_tip_bridge

Cosmic.transcribe_subtitles = bool(sys.argv[1:])

# 确保根目录位置正确，用相对路径加载模型
ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = ROOT_DIR
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
                elif not claim_retry_request(request_id):
                    pass
                else:
                    retry_task = asyncio.create_task(retry_latest_audio())

            await asyncio.sleep(0.4)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            console.print(f"监听重试请求失败：{exc}", style="bright_red")
            await asyncio.sleep(1.0)


async def watch_abandon_requests():
    initial_payload = read_abandon_request()
    last_request_id = (
        initial_payload.get("request_id") if isinstance(initial_payload, dict) else None
    )

    while True:
        try:
            payload = read_abandon_request()
            request_id = payload.get("request_id") if isinstance(payload, dict) else None

            if request_id is not None and request_id != last_request_id:
                last_request_id = request_id
                if claim_abandon_request(request_id):
                    abandon_current_task()

            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            console.print(f"监听放弃请求失败：{exc}", style="bright_red")
            await asyncio.sleep(1.0)


async def watch_clear_history_requests():
    initial_payload = read_clear_history_request()
    last_request_id = (
        initial_payload.get("request_id") if isinstance(initial_payload, dict) else None
    )

    while True:
        try:
            payload = read_clear_history_request()
            request_id = payload.get("request_id") if isinstance(payload, dict) else None

            if request_id is not None and request_id != last_request_id:
                last_request_id = request_id
                if claim_clear_history_request(request_id):
                    cleared = clear_finalized_history()
                    if cleared > 0:
                        console.print(f"已清除最近上屏消息记录：{cleared} 条。")
                    else:
                        console.print("最近上屏消息记录本来就是空的。", style="dim")

            await asyncio.sleep(0.25)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            console.print(f"监听清除最近上屏请求失败：{exc}", style="bright_red")
            await asyncio.sleep(1.0)


async def main_mic():
    Cosmic.loop = asyncio.get_event_loop()
    Cosmic.queue_in = asyncio.Queue()
    Cosmic.queue_out = asyncio.Queue()
    vision_task = None
    retry_watcher_task = None
    abandon_watcher_task = None
    clear_history_watcher_task = None
    level_publisher_task = None
    tsf_bridge = get_tsf_speech_tip_bridge()

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
    abandon_watcher_task = asyncio.create_task(watch_abandon_requests())
    clear_history_watcher_task = asyncio.create_task(watch_clear_history_requests())
    level_publisher_task = asyncio.create_task(publish_overlay_levels())
    if tsf_bridge.enabled:
        if tsf_bridge.start(Cosmic.loop):
            console.print("TSF Speech TIP 实验 IPC 已启动", style="bright_black")
        else:
            console.print(
                f"TSF Speech TIP 实验 IPC 启动失败：{tsf_bridge.startup_error}",
                style="bright_yellow",
            )
    console.print("已就绪", style="green")

    try:
        while True:
            await recv_result()
    finally:
        tsf_bridge.stop()
        if level_publisher_task is not None:
            level_publisher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await level_publisher_task
        if retry_watcher_task is not None:
            retry_watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await retry_watcher_task
        if abandon_watcher_task is not None:
            abandon_watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await abandon_watcher_task
        if clear_history_watcher_task is not None:
            clear_history_watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await clear_history_watcher_task
        if vision_task is not None:
            with contextlib.suppress(Exception):
                await stop_vision_context_service(vision_task)


def init_mic():
    startup_slot_acquired = False
    if not Cosmic.transcribe_subtitles:
        startup_slot_acquired = prepare_replacement_startup(
            Path(ROOT_DIR),
            "core_client",
            lambda: terminate_python_script_processes(
                Path(ROOT_DIR) / "core_client.py", exclude_pid=os.getpid()
            ),
        )
        if not startup_slot_acquired:
            console.print("无法完成 CapsWriter 后台录音进程替换，本次启动已退出。", style="bright_red")
            return
    try:
        asyncio.run(main_mic())
    except KeyboardInterrupt:
        console.print("再见！")
    finally:
        if startup_slot_acquired:
            release_startup_slot(Path(ROOT_DIR), "core_client")
        print("...")


if __name__ == "__main__":
    init_mic()
