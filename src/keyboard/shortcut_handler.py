import asyncio
import time
from contextlib import contextmanager
from typing import Any, cast

import keyboard
from pycaw.pycaw import AudioUtilities

from src.infra.cosmic import Cosmic, console
from src.keyboard.pause_other_audio import audio_playering_app_name
from src.audio.send_audio import send_audio
from src.audio.stream import stream_reopen
from src.infra.config import ClientConfig as Config
from src.infra.gui_output import gui_event, gui_print
from src.infra.my_status import Status
from src.polish.context_settings import toggle_textbox_context_enabled

task = asyncio.Future()
status = Status("开始录音", spinner="point")
unpause_needed = False
record_shortcut_pressed = False
textbox_context_toggle_pressed = False
escape_abort_pressed = False
sessions = []
_debug_action_count = 0
_DEBUG_SLOW_MS = 250.0


def _debug_enabled(force: bool = False) -> bool:
    if force:
        return True
    try:
        from src.transcribe.qwen_audio_legacy.settings import (
            should_show_realtime_logs,
        )

        return should_show_realtime_logs()
    except Exception:
        return False


def _debug_log(message: str, *, force: bool = False) -> None:
    if not _debug_enabled(force):
        return
    try:
        console.print(f"[timing][hotkey] {message}", style="dim")
    except Exception:
        pass


@contextmanager
def _timed_step(name: str):
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        _debug_log(
            f"{name} {elapsed_ms:.1f}ms",
            force=elapsed_ms >= _DEBUG_SLOW_MS,
        )


def _task_is_running(task_obj: Any) -> bool:
    try:
        return task_obj is not None and not task_obj.done()
    except Exception:
        return False


def _has_unfinished_nonrecording_task() -> bool:
    if Cosmic.on:
        return False
    return (
        bool(getattr(Cosmic, "active_task_id", None))
        or bool(getattr(Cosmic, "transcribe_busy", False))
        or _task_is_running(getattr(Cosmic, "active_send_task", None))
        or _task_is_running(getattr(Cosmic, "active_polish_task", None))
    )


def _cancel_async_task(task_obj: Any) -> None:
    if not _task_is_running(task_obj):
        return
    try:
        if Cosmic.loop is not None:
            Cosmic.loop.call_soon_threadsafe(task_obj.cancel)
        else:
            task_obj.cancel()
    except Exception:
        pass


def _safe_session_process_name(session) -> str | None:
    process = getattr(session, "Process", None)
    if not process:
        return None
    try:
        return process.name()
    except Exception:
        return None


def _emit_status_overlay(action: str, state: str | None = None) -> None:
    if not Config.show_listening_overlay:
        return
    try:
        payload = {"action": action, "emitted_at": time.time()}
        if state:
            payload["state"] = state
        gui_event("status_overlay", **payload)
    except Exception:
        pass


def _active_provider_uses_streaming_input() -> bool:
    try:
        from src.provider.provider_config import provider_manager
        from src.transcribe.providers import make_provider

        provider_kind = provider_manager.get_active_provider_type()
        if not provider_kind:
            return False
        return make_provider(str(provider_kind).strip().lower()).supports_streaming_input()
    except Exception:
        return False


def shortcut_correct(e: keyboard.KeyboardEvent):
    # 在我的 Windows 电脑上，left ctrl 和 right ctrl 的 keycode 都是一样的，
    # keyboard 库按 keycode 判断触发
    # 即便设置 right ctrl 触发，在按下 left ctrl 时也会触发
    # 不过，虽然两个按键的 keycode 一样，但事件 e.name 是不一样的
    # 在这里加一个判断，如果 e.name 不是我们期待的按键，就返回
    keyboard_api = cast(Any, keyboard)
    key_expect = keyboard_api.normalize_name(
        Config.speech_recognition_shortcut
    ).replace("left ", "")
    key_name = e.name or ""
    key_actual = key_name.replace("left ", "")
    if key_expect != key_actual:
        return False
    return True


def mute_all_sessions():
    global sessions
    with _timed_step("mute:GetAllSessions"):
        sessions = AudioUtilities.GetAllSessions()
    muted_count = 0
    for session in sessions:
        process_name = _safe_session_process_name(session)
        # 排除 ffplay.exe
        if process_name != "ffplay.exe":
            try:
                volume = session.SimpleAudioVolume
                volume.SetMute(1, None)
                muted_count += 1
            except Exception:
                continue
    _debug_log(f"mute:sessions total={len(sessions)} muted={muted_count}")


def unmute_all_sessions():
    global sessions
    unmuted_count = 0
    for session in sessions:
        process_name = _safe_session_process_name(session)
        # 排除 ffplay.exe
        if process_name != "ffplay.exe":
            try:
                volume = session.SimpleAudioVolume
                volume.SetMute(0, None)
                unmuted_count += 1
            except Exception:
                continue
    _debug_log(f"unmute:sessions total={len(sessions)} unmuted={unmuted_count}")


def launch_task():
    action_start = time.perf_counter()
    with _timed_step("launch:abandon_unfinished_check"):
        if _has_unfinished_nonrecording_task():
            abandon_current_task()

    Cosmic.abandon_requested = False
    Cosmic.active_task_id = None
    # 开始任务时播放提示音
    if Config.play_start_music:
        with _timed_step("launch:play_start_music_import"):
            from src.keyboard.play_music import play_music

        with _timed_step("launch:play_start_music"):
            play_music(Config.start_music_path, Config.start_music_volume)

    if Config.only_enable_microphones_when_pressed_record_shortcut:
        # 重启音频流
        with _timed_step("launch:stream_reopen"):
            stream_reopen()
        if Cosmic.stream is not None:
            with _timed_step("launch:stream_start"):
                Cosmic.stream.start()

    # 记录开始时间
    t1 = time.time()

    # 将开始标志放入队列
    if Cosmic.loop is None:
        return
    with _timed_step("launch:queue_begin"):
        asyncio.run_coroutine_threadsafe(
            Cosmic.queue_in.put({"type": "begin", "time": t1, "data": None}),
            Cosmic.loop,
        )

    # 录音时静音其他音频播放
    if Config.mute_other_audio:
        with _timed_step("launch:mute_all_sessions"):
            mute_all_sessions()

    # 录音时暂停其他音频播放 且 有音频正在播放
    global unpause_needed
    if Config.pause_other_audio and not unpause_needed:
        with _timed_step("launch:audio_playering_app_name"):
            process_name = audio_playering_app_name()
        if process_name:
            if process_name != "ffplay.exe":
                with _timed_step("launch:keyboard_play_pause"):
                    keyboard.send("play/pause")
                unpause_needed = True

    # 通知录音线程可以向队列放数据了
    Cosmic.on = t1

    # 打印动画：正在录音
    with _timed_step("launch:status_start"):
        status.start()
    with _timed_step("launch:overlay_show_listening"):
        _emit_status_overlay("show", "listening")

    # 启动识别任务
    global task
    with _timed_step("launch:create_send_audio_task"):
        task = asyncio.run_coroutine_threadsafe(
            send_audio(),
            Cosmic.loop,
        )
    total_ms = (time.perf_counter() - action_start) * 1000.0
    _debug_log(f"launch end total={total_ms:.1f}ms", force=total_ms >= _DEBUG_SLOW_MS)


def cancel_task():
    action_start = time.perf_counter()
    # 通知停止录音，关掉滚动条
    Cosmic.on = False
    with _timed_step("cancel:status_stop"):
        status.stop()
    with _timed_step("cancel:overlay_hide"):
        _emit_status_overlay("hide")

    # 取消音频静音
    if Config.mute_other_audio:
        with _timed_step("cancel:unmute_all_sessions"):
            unmute_all_sessions()

    # 取消音频暂停
    global unpause_needed
    if Config.pause_other_audio and unpause_needed:
        with _timed_step("cancel:keyboard_play_pause"):
            keyboard.send("play/pause")
        unpause_needed = False

    # 发送取消任务的消息到队列
    if Cosmic.loop is None:
        return
    with _timed_step("cancel:queue_cancel"):
        asyncio.run_coroutine_threadsafe(
            Cosmic.queue_in.put({"type": "cancel", "time": time.time(), "data": None}),
            Cosmic.loop,
        )

    if Config.only_enable_microphones_when_pressed_record_shortcut:
        # 结束音频流
        if Cosmic.stream is not None:
            with _timed_step("cancel:stream_stop"):
                Cosmic.stream.stop()
            with _timed_step("cancel:stream_close"):
                Cosmic.stream.close()
    total_ms = (time.perf_counter() - action_start) * 1000.0
    _debug_log(f"cancel end total={total_ms:.1f}ms", force=total_ms >= _DEBUG_SLOW_MS)


def abandon_current_task() -> None:
    global record_shortcut_pressed

    Cosmic.abandon_requested = True
    active_task_id = getattr(Cosmic, "active_task_id", None)
    if active_task_id:
        Cosmic.abandoned_task_ids.add(str(active_task_id))

    was_recording = bool(Cosmic.on)
    if Cosmic.on:
        cancel_task()
    else:
        status.stop()
        _emit_status_overlay("hide")
        if Config.mute_other_audio:
            unmute_all_sessions()

        global unpause_needed
        if Config.pause_other_audio and unpause_needed:
            keyboard.send("play/pause")
            unpause_needed = False

    task_attrs = ["active_polish_task"]
    if not was_recording:
        task_attrs.append("active_send_task")
    for attr_name in task_attrs:
        active_task = getattr(Cosmic, attr_name, None)
        _cancel_async_task(active_task)

    record_shortcut_pressed = False


def finish_task():
    global task
    action_start = time.perf_counter()

    # 通知停止录音，关掉滚动条
    Cosmic.on = False
    with _timed_step("finish:status_stop"):
        status.stop()

    # 通知结束任务
    if Cosmic.loop is None:
        _emit_status_overlay("hide")
        return
    if not _active_provider_uses_streaming_input():
        with _timed_step("finish:overlay_show_transcribing"):
            _emit_status_overlay("show", "transcribing")
    with _timed_step("finish:queue_finish"):
        asyncio.run_coroutine_threadsafe(
            Cosmic.queue_in.put(
                {"type": "finish", "time": time.time(), "data": None},
            ),
            Cosmic.loop,
        )

    # 取消音频静音
    if Config.mute_other_audio:
        with _timed_step("finish:unmute_all_sessions"):
            unmute_all_sessions()

    # 结束任务时播放提示音
    if Config.play_stop_music:
        with _timed_step("finish:play_stop_music_import"):
            from src.keyboard.play_music import play_music

        with _timed_step("finish:play_stop_music"):
            play_music(Config.stop_music_path, Config.stop_music_volume)

    # 取消音频暂停
    global unpause_needed
    if Config.pause_other_audio and unpause_needed:
        with _timed_step("finish:keyboard_play_pause"):
            keyboard.send("play/pause")
        unpause_needed = False
    if Config.only_enable_microphones_when_pressed_record_shortcut:
        # 结束音频流
        if Cosmic.stream is not None:
            with _timed_step("finish:stream_stop"):
                Cosmic.stream.stop()
            with _timed_step("finish:stream_close"):
                Cosmic.stream.close()
    total_ms = (time.perf_counter() - action_start) * 1000.0
    _debug_log(f"finish end total={total_ms:.1f}ms", force=total_ms >= _DEBUG_SLOW_MS)


# ==================== 绑定 handler ===============================


def record_shortcut_handler(e: keyboard.KeyboardEvent) -> None:
    global _debug_action_count, record_shortcut_pressed
    _debug_action_count += 1
    event_time = getattr(e, "time", None)
    event_lag_ms = (
        max(0.0, (time.time() - float(event_time)) * 1000.0) if event_time else None
    )
    lag_text = f" lag={event_lag_ms:.1f}ms" if event_lag_ms is not None else ""
    _debug_log(
        f"event#{_debug_action_count} type={e.event_type} name={e.name}{lag_text} "
        f"record_shortcut_pressed={record_shortcut_pressed} recording={bool(Cosmic.on)}",
        force=(event_lag_ms is not None and event_lag_ms >= _DEBUG_SLOW_MS),
    )

    if not shortcut_correct(e):
        return

    if e.event_type == keyboard.KEY_UP:
        record_shortcut_pressed = False
        return
    if e.event_type != keyboard.KEY_DOWN or record_shortcut_pressed:
        return

    record_shortcut_pressed = True
    if Cosmic.on:
        finish_task()
    else:
        launch_task()


def _normalize_shortcut_name(shortcut: str) -> str:
    keyboard_api = cast(Any, keyboard)
    try:
        return keyboard_api.normalize_name(shortcut).replace("left ", "").strip()
    except Exception:
        return shortcut.strip().lower()


def textbox_context_toggle_handler(e: keyboard.KeyboardEvent) -> None:
    global textbox_context_toggle_pressed

    if e.event_type == keyboard.KEY_UP:
        textbox_context_toggle_pressed = False
        return
    if e.event_type != keyboard.KEY_DOWN or textbox_context_toggle_pressed:
        return

    textbox_context_toggle_pressed = True
    enabled = toggle_textbox_context_enabled()
    if enabled is None:
        console.print(
            "切换附加文本框上下文失败（请检查 config/polish/polish.yaml 权限或格式）",
            style="#ff5555",
        )
        return

    state_text = "启用" if enabled else "禁用"
    gui_event("context_toggle", target="textbox_context", enabled=enabled)
    console.print(f"已{state_text}附加文本框上下文。", style="#888888")


def escape_abort_handler(e: keyboard.KeyboardEvent) -> None:
    global escape_abort_pressed

    if e.event_type == keyboard.KEY_UP:
        escape_abort_pressed = False
        return
    if e.event_type != keyboard.KEY_DOWN or escape_abort_pressed:
        return

    escape_abort_pressed = True
    if Cosmic.on:
        abandon_current_task()
        gui_print("已请求放弃当前任务。", "#cc4444")


def bond_shortcut():
    keyboard.hook_key(
        Config.speech_recognition_shortcut, record_shortcut_handler, suppress=True
    )

    keyboard.hook_key("esc", escape_abort_handler, suppress=False)

    toggle_shortcut = Config.toggle_textbox_context_shortcut.strip()
    if not toggle_shortcut:
        return

    if _normalize_shortcut_name(toggle_shortcut) == _normalize_shortcut_name(
        Config.speech_recognition_shortcut
    ):
        console.print(
            "文本框上下文快捷键与录音快捷键冲突，已跳过绑定。请修改 config.toml。",
            style="#ff8800",
        )
        return

    try:
        keyboard.hook_key(
            toggle_shortcut, textbox_context_toggle_handler, suppress=False
        )
    except Exception as exc:
        console.print(f"绑定文本框上下文快捷键失败：{exc}", style="#ff5555")
