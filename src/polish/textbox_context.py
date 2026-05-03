from __future__ import annotations

import ctypes
from collections.abc import Iterable
from functools import lru_cache
import platform
import time
import uuid
from dataclasses import dataclass
from ctypes import wintypes
from typing import Any

import clipman
import keyboard


EM_GETPASSWORDCHAR = 0x00D2
_MAX_DIRECT_TEXT_CHARS = 50000


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
    process_id: int | None = None
    process_name: str | None = None


def get_active_textbox_context(
    *,
    debug: bool = False,
    excluded_process_names: Iterable[str] | None = None,
) -> TextBoxContext | None:
    if platform.system() != "Windows":
        _debug_log(debug, "[文本框解析] 非 Windows 平台，跳过文本框读取。")
        return None

    hwnd = _get_focused_hwnd()
    class_name = _get_class_name(hwnd)
    process_id = _get_window_process_id(hwnd)
    process_name = _safe_process_name(process_id)
    _debug_log(
        debug,
        (
            "[文本框解析] 开始读取活动文本框"
            f" hwnd={_format_hwnd(hwnd)} class={class_name or '-'}"
            f" process={process_name or '-'} pid={process_id or '-'}"
        ),
    )

    if _is_excluded_process(process_name, excluded_process_names):
        _debug_log(
            debug,
            f"[文本框解析] 进程 {process_name} 命中上下文黑名单，跳过文本框读取。",
        )
        return None

    text, source, hwnd, class_name, is_password = _read_text_via_uia(
        hwnd,
        class_name,
        debug=debug,
    )
    if hwnd and (process_id is None or process_name is None):
        process_id = process_id or _get_window_process_id(hwnd)
        process_name = process_name or _safe_process_name(process_id)
    if text and text.strip() and source:
        _debug_log(
            debug,
            (
                "[文本框解析] UIA 读取成功"
                f" source={source} hwnd={_format_hwnd(hwnd)} class={class_name or '-'}"
                f" len={len(text)}"
            ),
        )
        return TextBoxContext(
            text=text,
            source=source,
            hwnd=hwnd,
            class_name=class_name,
            process_id=process_id,
            process_name=process_name,
        )

    if not is_password:
        _debug_log(debug, "[文本框解析] UIA 未获得文本，尝试剪贴板回退。")
        text = _read_text_via_clipboard_copy(debug=debug)
        if text and text.strip():
            _debug_log(debug, f"[文本框解析] 剪贴板回退成功 len={len(text)}")
            return TextBoxContext(
                text=text,
                source="clipboard",
                hwnd=hwnd,
                class_name=class_name,
                process_id=process_id,
                process_name=process_name,
            )

        _debug_log(debug, "[文本框解析] 剪贴板回退未获得文本。")
    else:
        _debug_log(debug, "[文本框解析] 检测到密码控件，跳过剪贴板回退。")

    _debug_log(debug, "[文本框解析] 未能读取当前文本框内容。")

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


def _get_window_process_id(hwnd: int | None) -> int | None:
    if not hwnd:
        return None

    try:
        process_id = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(
            wintypes.HWND(hwnd),
            ctypes.byref(process_id),
        )
    except Exception:
        return None

    return int(process_id.value) or None


def _safe_process_name(process_id: int | None) -> str | None:
    if not process_id:
        return None

    try:
        import psutil

        name = psutil.Process(process_id).name()
    except Exception:
        return None

    return name.strip() or None


def _is_excluded_process(
    process_name: str | None,
    excluded_process_names: Iterable[str] | None,
) -> bool:
    if not process_name or not excluded_process_names:
        return False

    process_name_norm = process_name.strip().casefold()
    if not process_name_norm:
        return False

    for excluded in excluded_process_names:
        if not isinstance(excluded, str):
            continue
        if process_name_norm == excluded.strip().casefold():
            return True

    return False


def _is_password_control(hwnd: int | None) -> bool:
    if not hwnd:
        return False
    try:
        password_char = ctypes.windll.user32.SendMessageW(hwnd, EM_GETPASSWORDCHAR, 0, 0)
        return bool(password_char)
    except Exception:
        return False


@lru_cache(maxsize=1)
def _get_uia_client() -> tuple[Any, Any]:
    import comtypes.client

    comtypes.client.GetModule("UIAutomationCore.dll")
    from comtypes.gen import UIAutomationClient as uiac

    return comtypes.client, uiac


def _read_text_via_uia(
    hwnd_hint: int | None,
    class_name_hint: str | None,
    *,
    debug: bool = False,
) -> tuple[str | None, str | None, int | None, str | None, bool]:
    hwnd = hwnd_hint
    class_name = class_name_hint
    is_password = _is_password_control(hwnd)

    try:
        import comtypes

        _debug_log(debug, "[文本框解析] 初始化 UI Automation。")
        comtypes.CoInitialize()
        try:
            comtypes_client, uiac = _get_uia_client()
            automation = comtypes_client.CreateObject(
                uiac.CUIAutomation,
                interface=uiac.IUIAutomation,
            )
            element = automation.GetFocusedElement()
            if not element:
                _debug_log(debug, "[文本框解析] GetFocusedElement 返回空。")
                return None, None, hwnd, class_name, is_password

            hwnd = _safe_int_property(element, "CurrentNativeWindowHandle") or hwnd
            class_name = _safe_string_property(element, "CurrentClassName") or class_name
            if hwnd and not class_name:
                class_name = _get_class_name(hwnd)

            _debug_log(
                debug,
                (
                    "[文本框解析] 已解析焦点元素"
                    f" hwnd={_format_hwnd(hwnd)} class={class_name or '-'}"
                ),
            )

            is_password = _is_password_element(element) or _is_password_control(hwnd)
            if is_password:
                _debug_log(debug, "[文本框解析] 焦点元素被识别为密码控件。")
                return None, None, hwnd, class_name, True

            for source, reader in (
                ("uia_text", _read_text_via_text_pattern),
                ("uia_value", _read_text_via_value_pattern),
                ("uia_legacy", _read_text_via_legacy_pattern),
            ):
                _debug_log(debug, f"[文本框解析] 尝试 {source}。")
                text = reader(element, uiac, debug=debug)
                if text and text.strip():
                    return text, source, hwnd, class_name, False

            _debug_log(debug, "[文本框解析] 所有 UIA 模式均未返回可用文本。")
        finally:
            comtypes.CoUninitialize()
    except Exception as exc:
        _debug_log(
            debug,
            f"[文本框解析] UIA 读取异常：{type(exc).__name__}: {exc}",
            style="yellow",
        )
        return None, None, hwnd, class_name, is_password

    return None, None, hwnd, class_name, is_password


def _read_text_via_text_pattern(element: Any, uiac: Any, *, debug: bool = False) -> str | None:
    pattern = _query_pattern(
        element,
        uiac.UIA_TextPatternId,
        uiac.IUIAutomationTextPattern,
        pattern_name="TextPattern",
        debug=debug,
    )
    if not pattern:
        return None

    try:
        text_range = pattern.DocumentRange
        if not text_range:
            _debug_log(debug, "[文本框解析] TextPattern 存在，但 DocumentRange 为空。")
            return None
        text = _normalize_text(text_range.GetText(_MAX_DIRECT_TEXT_CHARS))
        if not text or not text.strip():
            _debug_log(debug, "[文本框解析] TextPattern 返回空文本。")
            return None
        _debug_log(debug, f"[文本框解析] TextPattern 成功 len={len(text)}")
        return text
    except Exception as exc:
        _debug_log(
            debug,
            f"[文本框解析] TextPattern 读取失败：{type(exc).__name__}: {exc}",
            style="yellow",
        )
        return None


def _read_text_via_value_pattern(element: Any, uiac: Any, *, debug: bool = False) -> str | None:
    pattern = _query_pattern(
        element,
        uiac.UIA_ValuePatternId,
        uiac.IUIAutomationValuePattern,
        pattern_name="ValuePattern",
        debug=debug,
    )
    if not pattern:
        return None

    try:
        text = _normalize_text(pattern.CurrentValue)
        if not text or not text.strip():
            _debug_log(debug, "[文本框解析] ValuePattern 返回空文本。")
            return None
        _debug_log(debug, f"[文本框解析] ValuePattern 成功 len={len(text)}")
        return text
    except Exception as exc:
        _debug_log(
            debug,
            f"[文本框解析] ValuePattern 读取失败：{type(exc).__name__}: {exc}",
            style="yellow",
        )
        return None


def _read_text_via_legacy_pattern(element: Any, uiac: Any, *, debug: bool = False) -> str | None:
    pattern = _query_pattern(
        element,
        uiac.UIA_LegacyIAccessiblePatternId,
        uiac.IUIAutomationLegacyIAccessiblePattern,
        pattern_name="LegacyIAccessible",
        debug=debug,
    )
    if not pattern:
        return None

    try:
        text = _normalize_text(pattern.CurrentValue)
        if not text or not text.strip():
            _debug_log(debug, "[文本框解析] LegacyIAccessible 返回空文本。")
            return None
        _debug_log(debug, f"[文本框解析] LegacyIAccessible 成功 len={len(text)}")
        return text
    except Exception as exc:
        _debug_log(
            debug,
            f"[文本框解析] LegacyIAccessible 读取失败：{type(exc).__name__}: {exc}",
            style="yellow",
        )
        return None


def _query_pattern(
    element: Any,
    pattern_id: int,
    interface: Any,
    *,
    pattern_name: str,
    debug: bool = False,
) -> Any | None:
    try:
        pattern = element.GetCurrentPattern(pattern_id)
    except Exception as exc:
        _debug_log(
            debug,
            f"[文本框解析] {pattern_name} GetCurrentPattern 失败：{type(exc).__name__}: {exc}",
            style="yellow",
        )
        return None

    if not pattern:
        _debug_log(debug, f"[文本框解析] {pattern_name} 不受支持或返回空 COM 指针。")
        return None

    try:
        return pattern.QueryInterface(interface)
    except Exception as exc:
        _debug_log(
            debug,
            f"[文本框解析] {pattern_name} QueryInterface 失败：{type(exc).__name__}: {exc}",
            style="yellow",
        )
        return None


def _is_password_element(element: Any) -> bool:
    try:
        return bool(getattr(element, "CurrentIsPassword", False))
    except Exception:
        return False


def _coerce_int(value: object) -> int | None:
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        coerced = int(value)
    except Exception:
        return None
    return coerced or None


def _safe_int_property(obj: object, name: str) -> int | None:
    try:
        return _coerce_int(getattr(obj, name))
    except Exception:
        return None


def _safe_string_property(obj: object, name: str) -> str | None:
    try:
        return _safe_string(getattr(obj, name))
    except Exception:
        return None


def _safe_string(value: object) -> str | None:
    if isinstance(value, str) and value:
        return value
    return None


def _normalize_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None

    text = value.replace("\x00", "")
    return text if text else None


def _read_text_via_clipboard_copy(*, debug: bool = False) -> str | None:
    try:
        clipman.init()
        original = clipman.get()
    except Exception as exc:
        _debug_log(
            debug,
            f"[文本框解析] 剪贴板初始化失败：{type(exc).__name__}: {exc}",
            style="yellow",
        )
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
        elif current == sentinel:
            _debug_log(debug, "[文本框解析] 剪贴板探测未覆盖哨兵值。")
        else:
            _debug_log(debug, "[文本框解析] 剪贴板探测返回非字符串或空结果。")
        keyboard.send("right")
        time.sleep(0.02)
    except Exception as exc:
        _debug_log(
            debug,
            f"[文本框解析] 剪贴板回退异常：{type(exc).__name__}: {exc}",
            style="yellow",
        )
        copied = None
    finally:
        try:
            clipman.set(original)
        except Exception as exc:
            _debug_log(
                debug,
                f"[文本框解析] 恢复剪贴板失败：{type(exc).__name__}: {exc}",
                style="yellow",
            )
            pass

    if isinstance(copied, str) and not copied.strip():
        _debug_log(debug, "[文本框解析] 剪贴板回退仅获得空白文本。")

    return copied


def _debug_log(debug: bool, message: str, *, style: str = "dim") -> None:
    # Textbox context diagnostics are intentionally silenced to avoid noisy
    # terminal and GUI output during normal typing flows.
    return


def _format_hwnd(hwnd: int | None) -> str:
    if hwnd is None:
        return "-"
    return hex(hwnd)
