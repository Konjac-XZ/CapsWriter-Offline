import io
import sys
from asyncio import AbstractEventLoop, Queue
from typing import List, Union

import sounddevice as sd
import websockets
from rich.console import Console
from rich.theme import Theme

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
my_theme = Theme({"markdown.code": "cyan", "markdown.item.number": "yellow"})
console = Console(highlight=False, soft_wrap=False, theme=my_theme)


class Cosmic:
    """
    用一个 class 存储需要跨模块访问的变量值，命名为 Cosmic
    """

    on = False
    queue_in: Queue
    queue_out: Queue
    loop: Union[None, AbstractEventLoop] = None
    websocket: websockets.WebSocketClientProtocol = None
    audio_files = {}
    stream: Union[None, sd.InputStream] = None
    kwd_list: List[str] = []
    transcribe_subtitles = False
    online_translate_needed = False
    offline_translate_needed = False
    opposite_state = False
    # Whether we've already shown the non-USB-device warning during this run
    usb_warning_shown = False
