import io
import sys
from asyncio import AbstractEventLoop, Queue, Task
from typing import Union, cast, Any

import sounddevice as sd
import websockets
from rich.console import Console
from rich.theme import Theme
from . import gui_output
from .runtime_logging import record_console_message

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
my_theme = Theme({"markdown.code": "cyan", "markdown.item.number": "yellow"})
console = Console(highlight=False, soft_wrap=False, theme=my_theme)


# Preserve original print method
_console_print_orig = console.print


def _console_print_wrapper(*args, **kwargs):
    """Replacement for Console.print that also emits a GUI-friendly line.

    Behavior:
    - Join args into a single string separated by spaces.
    - If kwargs contains a 'style' parameter, forward it as color to GUI.
    - Always call the original console.print so terminal output is unchanged.
    """
    try:
        # Build a textual representation of the args similar to print()
        # Handle rich.console.NewLine specially (avoid sending its repr)
        try:
            from rich.console import NewLine

            def _arg_to_text(a):
                return "" if isinstance(a, NewLine) else str(a)
        except Exception:

            def _arg_to_text(a):
                # Fallback: avoid obvious NewLine reprs
                try:
                    if (
                        a.__class__.__name__ == "NewLine"
                        and a.__class__.__module__.startswith("rich")
                    ):
                        return ""
                except Exception:
                    pass
                return str(a)

        text = " ".join(_arg_to_text(a) for a in args)
        # Detect a style kwarg commonly used in this codebase
        color = kwargs.get("style")
        record_console_message(text, style=color)
        try:
            gui_output.gui_print(text, color=color)
        except Exception:
            # don't let logging break the app
            pass
    except Exception:
        pass
    # Only forward to the original rich Console.print when stdout is a TTY.
    # When stdout is a pipe (GUI captures it) we already emitted a structured
    # line via gui_output.gui_print and must avoid emitting the raw line again
    # to prevent duplicate appearance in the GUI.
    try:
        import sys

        if getattr(sys.stdout, "isatty", lambda: False)():
            return _console_print_orig(*args, **kwargs)
        else:
            # Non-TTY (stdout is a pipe—GUI captures it): we've already emitted
            # a structured line via gui_output.gui_print; avoid emitting the
            # raw/plain line to prevent duplicates in GUI.
            return
    except Exception:
        try:
            print(*args)
        except Exception:
            pass


# Monkeypatch the Console.print method for convenience across the codebase
setattr(console, "print", cast(Any, _console_print_wrapper))


class Cosmic:
    """
    用一个 class 存储需要跨模块访问的变量值，命名为 Cosmic
    """

    on: bool | float = False
    queue_in: Queue
    queue_out: Queue
    loop: Union[None, AbstractEventLoop] = None
    websocket: websockets.WebSocketClientProtocol | None = None
    audio_files = {}
    stream: Union[None, sd.InputStream] = None
    transcribe_subtitles = False
    # Whether we've already shown the non-USB-device warning during this run
    usb_warning_shown = False
    vision_context: dict[str, Any] = {}
    vision_context_task: Task[None] | None = None
    vision_context_last_error: str | None = None
    transcribe_busy = False
    abandon_requested = False
    active_task_id: str | None = None
    active_send_task: Task[Any] | None = None
    active_polish_task: Task[Any] | None = None
    active_reflection_task: Task[Any] | None = None
    reflection_worker_task: Task[Any] | None = None
    abandoned_task_ids: set[str] = set()
    _last_transcript_delta_len = 0
    _transcript_had_deltas = False
    _last_transcript_delta_task: str | None = None
