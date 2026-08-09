# coding: utf-8
import asyncio
import contextlib
import os
import signal
import sys
from collections.abc import Mapping
from pathlib import Path
from platform import system
from typing import cast

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
    ABANDON_REQUEST_PATH,
    CLEAR_HISTORY_REQUEST_PATH,
    claim_abandon_request,
    claim_clear_history_request,
    read_abandon_request,
    read_clear_history_request,
)
from src.audio.retry_cache import (
    RETRY_REQUEST_PATH,
    claim_retry_request,
    read_retry_request,
)
from src.audio.send_audio import retry_latest_audio
from src.audio.level_publisher import publish_overlay_levels
from src.audio.stream import stream_close, stream_open
from src.polish.llm_polish import clear_finalized_history
from src.personalization.reflection import run_reflection_worker
from src.polish.vision_context import (
    start_vision_context_service,
    stop_vision_context_service,
)
from src.system.empty_working_set import empty_current_working_set
from src.system.process_cleanup import terminate_python_script_processes
from src.system.startup_replacement import (
    prepare_replacement_startup,
    release_startup_slot,
)
from src.infra.file_change_signal import AsyncFileChangeSignal
from src.tsf_ipc import get_tsf_speech_tip_bridge
from src.gui_api import (
    gui_protocol_enabled,
    publish_gui_snapshots,
    run_gui_command_loop,
)

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


def _request_id(payload: object) -> object | None:
    if not isinstance(payload, Mapping):
        return None
    return cast(Mapping[str, object], payload).get("request_id")


async def watch_control_requests() -> None:
    """Handle GUI control files without continuously polling them while idle."""
    last_request_ids = {
        "retry": _request_id(read_retry_request()),
        "abandon": _request_id(read_abandon_request()),
        "clear_history": _request_id(read_clear_history_request()),
    }
    retry_task: asyncio.Task | None = None
    change_signal = AsyncFileChangeSignal(
        asyncio.get_running_loop(),
        (RETRY_REQUEST_PATH, ABANDON_REQUEST_PATH, CLEAR_HISTORY_REQUEST_PATH),
    )
    observer_started = False
    fallback_interval = 0.25
    try:
        try:
            change_signal.start()
            observer_started = True
        except Exception as exc:
            console.print(
                f"控制请求文件通知不可用，已切换低频轮询：{exc}",
                style="bright_yellow",
            )

        while True:
            change_signal.clear()
            handled_request = False
            try:
                retry_request_id = _request_id(read_retry_request())
                if (
                    retry_request_id is not None
                    and retry_request_id != last_request_ids["retry"]
                ):
                    handled_request = True
                    last_request_ids["retry"] = retry_request_id
                    if Cosmic.on or getattr(Cosmic, "transcribe_busy", False):
                        console.print(
                            "当前正在识别，稍后再重试。", style="bright_yellow"
                        )
                    elif retry_task is not None and not retry_task.done():
                        console.print(
                            "当前已有重试请求正在进行。", style="bright_yellow"
                        )
                    elif claim_retry_request(retry_request_id):
                        retry_task = asyncio.create_task(retry_latest_audio())

                abandon_request_id = _request_id(read_abandon_request())
                if (
                    abandon_request_id is not None
                    and abandon_request_id != last_request_ids["abandon"]
                ):
                    handled_request = True
                    last_request_ids["abandon"] = abandon_request_id
                    if claim_abandon_request(abandon_request_id):
                        abandon_current_task()

                clear_request_id = _request_id(read_clear_history_request())
                if (
                    clear_request_id is not None
                    and clear_request_id != last_request_ids["clear_history"]
                ):
                    handled_request = True
                    last_request_ids["clear_history"] = clear_request_id
                    if claim_clear_history_request(clear_request_id):
                        cleared = clear_finalized_history()
                        if cleared > 0:
                            console.print(f"已清除最近上屏消息记录：{cleared} 条。")
                        else:
                            console.print("最近上屏消息记录本来就是空的。", style="dim")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                console.print(f"监听控制请求失败：{exc}", style="bright_red")

            if observer_started:
                # A long timeout is only a safety net for lost OS notifications.
                await change_signal.wait(timeout=30.0)
            else:
                if handled_request:
                    fallback_interval = 0.25
                await asyncio.sleep(fallback_interval)
                fallback_interval = min(1.0, fallback_interval * 2.0)
    finally:
        change_signal.stop()


async def main_mic():
    Cosmic.loop = asyncio.get_event_loop()
    Cosmic.queue_in = asyncio.Queue()
    Cosmic.queue_out = asyncio.Queue()
    vision_task = None
    control_watcher_task = None
    level_publisher_task = None
    reflection_worker_task = None
    gui_command_task = None
    gui_snapshot_task = None
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
    control_watcher_task = asyncio.create_task(watch_control_requests())
    level_publisher_task = asyncio.create_task(publish_overlay_levels())
    reflection_worker_task = asyncio.create_task(
        run_reflection_worker(),
        name="personalization_reflection_worker",
    )
    Cosmic.reflection_worker_task = reflection_worker_task
    if gui_protocol_enabled():
        gui_command_task = asyncio.create_task(
            run_gui_command_loop(), name="native_gui_command_loop"
        )
        gui_snapshot_task = asyncio.create_task(
            publish_gui_snapshots(), name="native_gui_status_publisher"
        )
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
        if reflection_worker_task is not None:
            reflection_worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reflection_worker_task
            if Cosmic.reflection_worker_task is reflection_worker_task:
                Cosmic.reflection_worker_task = None
        if gui_snapshot_task is not None:
            gui_snapshot_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await gui_snapshot_task
        if gui_command_task is not None:
            gui_command_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await gui_command_task
        if control_watcher_task is not None:
            control_watcher_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await control_watcher_task
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
            console.print(
                "无法完成 CapsWriter 后台录音进程替换，本次启动已退出。",
                style="bright_red",
            )
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
