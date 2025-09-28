from pathlib import Path
from unittest import result

import win32gui

from util.config import ClientConfig as Config


def encode_booleans(*args: bool) -> int:
    """
    将多个布尔值参数转换为一个整数，通过位运算对每个参数的状态进行编码。
    每个布尔值代表一个二进制位，True 将对应的位设为 1，False 则保持为 0。
    """
    result = 0
    for i, arg in enumerate(args):
        if arg:
            result |= 1 << i
    return result


def send_signal(hwnd: int, message: int, WPARAM: int, LPARAM: int):
    try:
        # print("Before SendMessage")
        result = win32gui.PostMessage(hwnd, message, WPARAM, LPARAM)
        # print("After SendMessage, result:", result)
        if result:
            # print("Message posted successfully.")
            return True
        else:
            # print(f"Failed to post message, error code: {result}")
            return result
    except Exception:
        # print("Exception in send_signal:", e)
        return None


def send_signal_to_hint_while_recording(
    is_microphone_in_use: bool,
    is_short_duration: bool,
    hold_mode: bool,
):
    exe_path = Path().cwd() / "hint_while_recording.exe"
    hwnd = win32gui.FindWindow(
        "AutoHotkey",
        str(exe_path),
    )
    if not Config.hint_while_recording_at_edit_position_powered_by_ahk:
        return "Feature not enabled"

    if hwnd:
        # print("Found window, handle is:", hwnd)
        encoded_bools = encode_booleans(
            is_microphone_in_use,
            is_short_duration,
            hold_mode,
        )
        result = send_signal(hwnd, 0x5555, encoded_bools, 0)
    else:
        # print("Window not found")
        result = "Window not found"
    return result


def list_all_window(hwnd: int, extra: None):
    try:
        title = win32gui.GetWindowText(hwnd)
        className = win32gui.GetClassName(hwnd)
        print(f"Hwnd: {hwnd}\nTitle: {title}\nClass: {className}\n\n")
    except Exception as e:
        print(f"Error retrieving window info: {e}")
    return True


if __name__ == "__main__":
    # win32gui.EnumWindows(list_all_window, None)
    result = send_signal_to_hint_while_recording(
        True,
        True,
        True,
    )
    print(result)
