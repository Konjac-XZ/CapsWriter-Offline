# coding: utf-8
import sys, importlib.util, platform
print(sys.executable, sys.version, platform.architecture())
print(importlib.util.find_spec('_cffi_backend'))
print([p for p in sys.path if p.endswith('site-packages')])

import asyncio
import os
import signal
import sys
from pathlib import Path
from platform import system
from typing import List

import colorama
import typer

from util.client_cosmic import Cosmic, console
from util.config import ClientConfig as Config

if sys.argv[1:]:
    Cosmic.transcribe_subtitles = True
else:
    Cosmic.transcribe_subtitles = False
from util.client_adjust_srt import adjust_srt
from util.client_hot_update import observe_hot, update_hot_all
from util.client_recv_result import recv_result
from util.client_shortcut_handler import bond_shortcut
from util.client_show_tips import show_file_tips, show_mic_tips
from util.client_stream import stream_close, stream_open
from util.client_transcribe import transcribe_check, transcribe_recv, transcribe_send
from util.empty_working_set import empty_current_working_set

# 确保根目录位置正确，用相对路径加载模型
BASE_DIR = os.getcwd()
os.chdir(BASE_DIR)
# BASE_DIR = os.path.dirname(__file__); os.chdir(BASE_DIR)

# 确保终端能使用 ANSI 控制字符
colorama.init()

# MacOS 的权限设置
if system() == "Darwin" and not sys.argv[1:]:
    if os.getuid() != 0:
        print("在 MacOS 上需要以管理员启动客户端才能监听键盘活动，请 sudo 启动")
        input("按回车退出")
        sys.exit()
    else:
        os.umask(0o000)


async def main_mic():
    Cosmic.loop = asyncio.get_event_loop()
    Cosmic.queue_in = asyncio.Queue()
    Cosmic.queue_out = asyncio.Queue()

    show_mic_tips()

    # 打开音频流
    Cosmic.stream = stream_open()

    # Ctrl-C 关闭音频流，触发自动重启
    signal.signal(signal.SIGINT, stream_close)

    # 绑定按键
    bond_shortcut()

    # 清空物理内存工作集
    if system() == "Windows":
        empty_current_working_set()

    while True:
        await recv_result()


def init_mic():
    try:
        asyncio.run(main_mic())
    except KeyboardInterrupt:
        console.print("再见！")
    finally:
        print("...")


if __name__ == "__main__":
    init_mic()
