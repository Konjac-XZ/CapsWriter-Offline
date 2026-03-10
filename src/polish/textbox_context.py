from __future__ import annotations

import ctypes
import platform
import time
import uuid
from dataclasses import dataclass
from ctypes import wintypes

import clipman
import keyboard


WM_GETTEXT = 0x000D
WM_GETTEXTLENGTH = 0x000E
EM_GETPASSWORDCHAR = 0x00D2
_MAX_DIRECT_TEXT_CHARS = 50000
_TEXT_INPUT_CLASS_MARKERS = (
    "edit",
    "richedit",
    "scintilla",
    "tmemo",
    "tedit",
)


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class GUITHREADINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", RECT),
    ]


@dataclass(slots=True)
class TextBoxContext:
    text: str
    source: str
    hwnd: int | None = None
    class_name: str | None = None


def get_active_textbox_context(
    *,
    allow_clipboard_fallback: bool = False,
) -> TextBoxContext | None:
    if platform.system() != "Windows":
        return None

    hwnd = _get_focused_hwnd()
    class_name = _get_class_name(hwnd)

    text = _read_text_via_window_messages(hwnd, class_name)
    if text and text.strip():
        return TextBoxContext(
            text=text,
            source="win32",
            hwnd=hwnd,
            class_name=class_name,
        )

    if allow_clipboard_fallback:
        text = _read_text_via_clipboard_copy()
        if text and text.strip():
            return TextBoxContext(
                text=text,
                source="clipboard",
                hwnd=hwnd,
                class_name=class_name,
            )

    return None


def _get_focused_hwnd() -> int | None:
    user32 = ctypes.windll.user32
    info = GUITHREADINFO()
    info.cbSize = ctypes.sizeof(GUITHREADINFO)
    if user32.GetGUIThreadInfo(0, ctypes.byref(info)):
        hwnd = info.hwndFocus or info.hwndCaret
        if hwnd:
            return int(hwnd)
    return None


def _get_class_name(hwnd: int | None) -> str | None:
    if not hwnd:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(256)
        length = ctypes.windll.user32.GetClassNameW(hwnd, buffer, len(buffer))
        if length > 0:
            return buffer.value
    except Exception:
        return None
    return None


def _is_password_control(hwnd: int | None) -> bool:
    if not hwnd:
        return False
    try:
        password_char = ctypes.windll.user32.SendMessageW(hwnd, EM_GETPASSWORDCHAR, 0, 0)
        return bool(password_char)
    except Exception:
        return False


def _looks_like_text_input_class(class_name: str | None) -> bool:
    if not class_name:
        return False
    lowered = class_name.lower()
    return any(marker in lowered for marker in _TEXT_INPUT_CLASS_MARKERS)


def _read_text_via_window_messages(hwnd: int | None, class_name: str | None) -> str | None:
    if not hwnd or _is_password_control(hwnd):
        return None
    if not _looks_like_text_input_class(class_name):
        return None

    user32 = ctypes.windll.user32
    try:
        if not user32.IsWindow(hwnd):
            return None

        length = int(user32.SendMessageW(hwnd, WM_GETTEXTLENGTH, 0, 0) or 0)
        if length <= 0:
            length = int(user32.GetWindowTextLengthW(hwnd) or 0)
        if length <= 0:
            return None

        length = min(length, _MAX_DIRECT_TEXT_CHARS)
        buffer = ctypes.create_unicode_buffer(length + 1)
        copied = int(user32.SendMessageW(hwnd, WM_GETTEXT, length + 1, buffer) or 0)
        if copied <= 0:
            copied = int(user32.GetWindowTextW(hwnd, buffer, length + 1) or 0)
        if copied <= 0:
            return None

        text = buffer.value
        return text if text else None
    except Exception:
        return None


def _read_text_via_clipboard_copy() -> str | None:
    try:
        clipman.init()
        original = clipman.get()
    except Exception:
        return None

    sentinel = f"__capswriter_clipboard_probe__{uuid.uuid4()}__"
    copied: str | None = None
    try:
        clipman.set(sentinel)
        time.sleep(0.03)
        keyboard.send("ctrl+a")
        time.sleep(0.03)
        keyboard.send("ctrl+c")
        time.sleep(0.12)
        current = clipman.get()
        if isinstance(current, str) and current != sentinel:
            copied = current
        keyboard.send("right")
        time.sleep(0.02)
    except Exception:
        copied = None
    finally:
        try:
            clipman.set(original)
        except Exception:
            pass

    return copied
