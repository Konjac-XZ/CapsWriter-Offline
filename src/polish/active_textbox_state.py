from __future__ import annotations

import ctypes
from collections.abc import Iterable
from ctypes import wintypes
from dataclasses import dataclass
import logging
import platform
import time
from typing import Any


_LOGGER = logging.getLogger("capswriter.polish.active_textbox_state")
_GA_ROOT = 2


class _Rect(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", _Rect),
    ]


@dataclass(frozen=True, slots=True)
class ActiveTextBoxState:
    """Best-effort metadata for the control receiving the user's input."""

    source: str
    process_id: int | None = None
    process_name: str | None = None
    window_title: str | None = None
    window_class_name: str | None = None
    control_name: str | None = None
    control_class_name: str | None = None
    control_type: str | None = None
    automation_id: str | None = None
    is_enabled: bool | None = None
    is_keyboard_focusable: bool | None = None
    has_keyboard_focus: bool | None = None
    is_password: bool | None = None


def get_active_textbox_state(
    *,
    debug: bool = False,
    excluded_process_names: Iterable[str] | None = None,
) -> ActiveTextBoxState | None:
    """Capture focused-control metadata, with Win32 focus/foreground fallbacks."""
    started = time.perf_counter()
    if platform.system() != "Windows":
        _LOGGER.info("Active textbox state skipped reason=non_windows")
        return None

    fallback_reason: str | None = None
    try:
        state = _capture_via_uia()
    except Exception as exc:  # noqa: BLE001 - metadata capture is best effort
        _debug_log(debug, "uia", exc)
        fallback_reason = f"uia_error:{type(exc).__name__}"
        state = None

    if state is None or not _has_useful_state(state):
        if fallback_reason is None:
            fallback_reason = "uia_empty"
        _LOGGER.info(
            "Active textbox state fallback from=uia to=win32 reason=%s",
            fallback_reason,
        )
        try:
            state = _capture_via_win32()
        except Exception as exc:  # noqa: BLE001 - metadata capture is best effort
            _debug_log(debug, "win32", exc)
            _LOGGER.info(
                "Active textbox state unavailable outcome=error elapsed_ms=%.1f",
                (time.perf_counter() - started) * 1000.0,
            )
            return None

    if state is None:
        _LOGGER.info(
            "Active textbox state unavailable outcome=empty elapsed_ms=%.1f",
            (time.perf_counter() - started) * 1000.0,
        )
        return None
    if _is_excluded_process(
        state.process_name,
        excluded_process_names,
    ):
        _LOGGER.info(
            "Active textbox state skipped reason=excluded_process process=%s source=%s elapsed_ms=%.1f",
            state.process_name or "unknown",
            state.source,
            (time.perf_counter() - started) * 1000.0,
        )
        return None
    _LOGGER.info(
        "Active textbox state captured source=%s process=%s fields=%s elapsed_ms=%.1f",
        state.source,
        state.process_name or "unknown",
        ",".join(_present_field_names(state)) or "none",
        (time.perf_counter() - started) * 1000.0,
    )
    return state


def _has_useful_state(state: ActiveTextBoxState) -> bool:
    return any(
        value is not None and value != ""
        for value in (
            state.process_id,
            state.process_name,
            state.window_title,
            state.window_class_name,
            state.control_name,
            state.control_class_name,
            state.control_type,
            state.automation_id,
            state.is_enabled,
            state.is_keyboard_focusable,
            state.has_keyboard_focus,
            state.is_password,
        )
    )


def _present_field_names(state: ActiveTextBoxState) -> tuple[str, ...]:
    fields = []
    for name in (
        "process_id",
        "process_name",
        "window_title",
        "window_class_name",
        "control_name",
        "control_class_name",
        "control_type",
        "automation_id",
        "is_enabled",
        "is_keyboard_focusable",
        "has_keyboard_focus",
        "is_password",
    ):
        if getattr(state, name) is not None:
            fields.append(name)
    return tuple(fields)


def _capture_via_uia() -> ActiveTextBoxState | None:
    import comtypes
    import comtypes.client

    comtypes.CoInitialize()
    try:
        comtypes.client.GetModule("UIAutomationCore.dll")
        from comtypes.gen import UIAutomationClient as uiac

        automation = comtypes.client.CreateObject(
            uiac.CUIAutomation,
            interface=uiac.IUIAutomation,
        )
        element = automation.GetFocusedElement()
        if not element:
            return None

        control_hwnd = _safe_int_property(element, "CurrentNativeWindowHandle")
        process_id = _safe_int_property(element, "CurrentProcessId")
        focus_hwnd = _get_gui_thread_focus_hwnd()
        foreground_hwnd = _get_foreground_hwnd()
        window_hwnd = _choose_root_window(
            control_hwnd,
            focus_hwnd,
            foreground_hwnd,
            process_id,
        )
        if process_id is None:
            process_id = _get_window_process_id(
                control_hwnd or focus_hwnd or window_hwnd
            )

        return ActiveTextBoxState(
            source="uia",
            process_id=process_id,
            process_name=_safe_process_name(process_id),
            window_title=_get_window_text(window_hwnd),
            window_class_name=_get_class_name(window_hwnd),
            control_name=_safe_string_property(element, "CurrentName"),
            control_class_name=(
                _safe_string_property(element, "CurrentClassName")
                or _get_class_name(control_hwnd or focus_hwnd)
            ),
            control_type=_safe_string_property(
                element,
                "CurrentLocalizedControlType",
            ),
            automation_id=_safe_string_property(element, "CurrentAutomationId"),
            is_enabled=_safe_bool_property(element, "CurrentIsEnabled"),
            is_keyboard_focusable=_safe_bool_property(
                element,
                "CurrentIsKeyboardFocusable",
            ),
            has_keyboard_focus=_safe_bool_property(
                element,
                "CurrentHasKeyboardFocus",
            ),
            is_password=_safe_bool_property(element, "CurrentIsPassword"),
        )
    finally:
        comtypes.CoUninitialize()


def _capture_via_win32() -> ActiveTextBoxState | None:
    focus_hwnd = _get_gui_thread_focus_hwnd()
    foreground_hwnd = _get_foreground_hwnd()
    control_hwnd = focus_hwnd or foreground_hwnd
    if not control_hwnd:
        return None

    window_hwnd = _get_root_window(control_hwnd) or foreground_hwnd or control_hwnd
    process_id = _get_window_process_id(control_hwnd)
    return ActiveTextBoxState(
        source="win32_focus" if focus_hwnd else "win32_foreground",
        process_id=process_id,
        process_name=_safe_process_name(process_id),
        window_title=_get_window_text(window_hwnd),
        window_class_name=_get_class_name(window_hwnd),
        control_class_name=_get_class_name(control_hwnd),
        has_keyboard_focus=True if focus_hwnd else None,
    )


def _choose_root_window(
    control_hwnd: int | None,
    focus_hwnd: int | None,
    foreground_hwnd: int | None,
    process_id: int | None,
) -> int | None:
    for hwnd in (control_hwnd, focus_hwnd):
        root = _get_root_window(hwnd)
        if root:
            return root
    if foreground_hwnd and (
        process_id is None or _get_window_process_id(foreground_hwnd) == process_id
    ):
        return foreground_hwnd
    return None


def _get_gui_thread_focus_hwnd() -> int | None:
    info = _GuiThreadInfo()
    info.cbSize = ctypes.sizeof(_GuiThreadInfo)
    if ctypes.windll.user32.GetGUIThreadInfo(0, ctypes.byref(info)):
        hwnd = info.hwndFocus or info.hwndCaret
        return int(hwnd) if hwnd else None
    return None


def _get_foreground_hwnd() -> int | None:
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    return int(hwnd) if hwnd else None


def _get_root_window(hwnd: int | None) -> int | None:
    if not hwnd:
        return None
    root = ctypes.windll.user32.GetAncestor(wintypes.HWND(hwnd), _GA_ROOT)
    return int(root) if root else int(hwnd)


def _get_window_process_id(hwnd: int | None) -> int | None:
    if not hwnd:
        return None
    process_id = wintypes.DWORD()
    ctypes.windll.user32.GetWindowThreadProcessId(
        wintypes.HWND(hwnd),
        ctypes.byref(process_id),
    )
    return int(process_id.value) or None


def _get_window_text(hwnd: int | None) -> str | None:
    if not hwnd:
        return None
    length = int(ctypes.windll.user32.GetWindowTextLengthW(wintypes.HWND(hwnd)))
    buffer = ctypes.create_unicode_buffer(max(1, length + 1))
    copied = ctypes.windll.user32.GetWindowTextW(
        wintypes.HWND(hwnd),
        buffer,
        len(buffer),
    )
    return buffer.value.strip() if copied > 0 and buffer.value.strip() else None


def _get_class_name(hwnd: int | None) -> str | None:
    if not hwnd:
        return None
    buffer = ctypes.create_unicode_buffer(256)
    copied = ctypes.windll.user32.GetClassNameW(
        wintypes.HWND(hwnd),
        buffer,
        len(buffer),
    )
    return buffer.value.strip() if copied > 0 and buffer.value.strip() else None


def _safe_process_name(process_id: int | None) -> str | None:
    if not process_id:
        return None
    try:
        import psutil

        name = psutil.Process(process_id).name()
    except Exception:
        return None
    return name.strip() or None


def _safe_string_property(element: Any, name: str) -> str | None:
    try:
        value = getattr(element, name)
    except Exception:
        return None
    if not isinstance(value, str):
        return None
    return value.strip() or None


def _safe_int_property(element: Any, name: str) -> int | None:
    try:
        value = int(getattr(element, name) or 0)
    except (AttributeError, TypeError, ValueError):
        return None
    return value or None


def _safe_bool_property(element: Any, name: str) -> bool | None:
    try:
        return bool(getattr(element, name))
    except Exception:
        return None


def _is_excluded_process(
    process_name: str | None,
    excluded_process_names: Iterable[str] | None,
) -> bool:
    if not process_name or not excluded_process_names:
        return False
    normalized = process_name.strip().casefold()
    return any(
        normalized == excluded.strip().casefold()
        for excluded in excluded_process_names
        if isinstance(excluded, str) and excluded.strip()
    )


def _debug_log(debug: bool, source: str, error: Exception) -> None:
    log = _LOGGER.warning if debug else _LOGGER.info
    log(
        "Active textbox state provider failed source=%s error_type=%s",
        source,
        type(error).__name__,
    )
