import asyncio
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event
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
pool = ThreadPoolExecutor()
pressed = False
released = True
event = Event()
unpause_needed = False
double_clicked = False
is_short_duration = False
hold_mode_first_time_cancel_task = False
last_time_pressed = 0
last_time_released = 0
key_pressed = False
textbox_context_toggle_pressed = False
escape_abort_pressed = False
sessions = []
_debug_action_count = 0
_DEBUG_SLOW_MS = 250.0


def _debug_enabled(force: bool = False) -> bool:
    return force


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


def shortcut_correct(e: keyboard.KeyboardEvent):
    # 在我的 Windows 电脑上，left ctrl 和 right ctrl 的 keycode 都是一样的，
    # keyboard 库按 keycode 判断触发
    # 即便设置 right ctrl 触发，在按下 left ctrl 时也会触发
    # 不过，虽然两个按键的 keycode 一样，但事件 e.name 是不一样的
    # 在这里加一个判断，如果 e.name 不是我们期待的按键，就返回
    keyboard_api = cast(Any, keyboard)
    key_expect = keyboard_api.normalize_name(Config.speech_recognition_shortcut).replace(
        "left ", ""
    )
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

    global hold_mode_first_time_cancel_task
    if (
        not double_clicked
        and Config.only_enable_microphones_when_pressed_record_shortcut
    ):
        # 重启音频流; 在双击情况下, 只在第一次的时候启动(单击模式)
        with _timed_step("launch:stream_reopen"):
            stream_reopen()
        if Cosmic.stream is not None:
            with _timed_step("launch:stream_start"):
                Cosmic.stream.start()

    # 长按模式(hold_mode)双击功能 第二次重启不适用于上面的判断, 因此，需要下面来判断是否重启音频流
    # 长按模式(hold_mode)双击功能 確實需要第二次啓動音频流, 设计的时候就是如此, 因为会进行一次 start  cancel 的流程, 然后第二次啓動才是双击功能的录音
    elif (
        hold_mode_first_time_cancel_task
        and double_clicked
        and Config.only_enable_microphones_when_pressed_record_shortcut
    ):
        with _timed_step("launch:stream_reopen_double_click"):
            stream_reopen()
        if Cosmic.stream is not None:
            with _timed_step("launch:stream_start_double_click"):
                Cosmic.stream.start()
        hold_mode_first_time_cancel_task = False
    # 记录开始时间
    t1 = time.time()

    # 将开始标志放入队列
    if Cosmic.loop is None:
        return
    with _timed_step("launch:queue_begin"):
        asyncio.run_coroutine_threadsafe(
            Cosmic.queue_in.put({"type": "begin", "time": t1, "data": None}), Cosmic.loop
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
    global double_clicked, key_pressed, is_short_duration, hold_mode_first_time_cancel_task

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

    double_clicked = False
    key_pressed = False
    is_short_duration = False
    hold_mode_first_time_cancel_task = False
    Cosmic.opposite_state = False


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


# =================单击模式======================


def click_mode(e: keyboard.KeyboardEvent):
    # 0. 原来的设计甚是巧妙巧妙, 但是我的功力有限，消化不良.
    # 1. 这里的设计思路是: 按下`录音键`只记录`按下时的时间标记`，然后根据`弹起来时的时间标记`和前面`按下时的时间标记`的 长短 进行判断应该进行哪一种行为.

    # 2. 这种方法可以处理以下的情况:
    # 2.1. 原来的设计: 使用`CapsLock`开启大小写的功能, 在单击模式下`未触发`录音模式之前,长按这个按键切换有机率失败，但是进行录音模式`之后`, 成功的几率极大.
    # 2.2. 这是因为: `def manage_task(e: Event): `它是按下按键就立刻开启任务，在开启任务之后才进行判断是否`长/短`按。这就是导致有几率失败的原因

    # 3. `長按` = 进行大小写切换的功能, 需要按键抬起后才能切换;
    # 3.1. 如果需要按下之后是根据按下(不需要抬起)的时间自动进行大小写切换的功能, 可以参考原来作者的代码`def count_down(e: Event):`

    # 4. 为了解决在 Windows 下按键会自动重复的问题 : key_pressed 变量用于追踪按键是否已经被按下并记录时间。当按键第一次被按下时，记录时间并将 key_pressed 设为 True，防止重复记录时间。当按键释放时，将 key_pressed 重新设为 False，允许下一次按键记录新的时间。

    global \
        last_time_pressed, \
        last_time_released, \
        key_pressed, \
        double_clicked, \
        is_short_duration, \
        unpause_needed

    if e.event_type == keyboard.KEY_DOWN and not key_pressed:
        # 計算是否屬於短時間內雙击`錄音鍵`
        is_short_duration = (
            True if time.time() - last_time_released < Config.threshold else False
        )

        last_time_pressed = time.time()
        key_pressed = True

    elif e.event_type == keyboard.KEY_UP:
        last_time_released = time.time()

        # 记录是否有任务; 此处已改用变量:`double_clicked` 来判断任务是否进行中
        # on = Cosmic.on

        # 如果大于`Config.threshold`的值, 判定为`長按`, 就取消本栈启动的任务(`cancel_task()`)
        if last_time_released - last_time_pressed >= Config.threshold:
            # 函数`cancel_task()` : 他和我想象中的功能可能不一样
            # 我想象中的功能: `長按` = 进行大小写切换
            # 原来的功能: 可能是 中断并且不输出 已经录入的语音文字
            # 如果启动以下的函数`cancel_task()` : Bug 复现方法是 按一次`录音键`进入录音状态, 随后进行一次长按, 就会进入错乱状态.
            # 如果没有特殊的需求, 现在的状况可以满足 `長按` = 进行大小写切换 的功能
            # 否则需要进入函数`cancel_task()` 修改
            # cancel_task()

            # 判定为`長按`，发送原來的按键功能
            keyboard.send(Config.speech_recognition_shortcut)
            key_pressed = False
            return

        # 任务不在进行中, 且不判定为`短击`, 就开始任务, 同时标记 任务在进行中狀态
        elif not double_clicked and not is_short_duration:
            launch_task()
            # `double_clicked`变量 在此处函数中 改为常駐 因此不需要以下的config判断
            # if Config.enable_double_click_opposite_state:
            double_clicked = True
            key_pressed = False

        # 任务在进行中, 且不判定为`短击`, 就结束和完成任务
        elif double_clicked and not is_short_duration:
            finish_task()
            # if Config.enable_double_click_opposite_state:
            double_clicked = False
            key_pressed = False
            return

        # 任务在进行中, 且为`短击`, 判定爲需要輸出 `簡/繁`, 并且结束函数
        elif (
            double_clicked and is_short_duration
            # and Config.enable_double_click_opposite_state
        ):
            Cosmic.opposite_state = not Cosmic.opposite_state
            key_pressed = False
            # return

        # print(f'世界的尽头!')


# ======================长按模式==================================


def hold_mode(e: keyboard.KeyboardEvent):
    """像对讲机一样，按下录音，松开停止"""
    global \
        task, \
        double_clicked, \
        last_time_pressed, \
        last_time_released, \
        hold_mode_first_time_cancel_task, \
        unpause_needed

    if e.event_type == "up" and not Cosmic.on:
        last_time_released = time.time()
        return

    # 計算是否屬於短時間內按下`錄音鍵`
    is_short_duration = (
        True if time.time() - last_time_released < Config.threshold else False
    )

    # 短時間內,按下第二次錄音鍵判定爲需要輸出 `簡/繁`
    if is_short_duration and Config.enable_double_click_opposite_state:
        double_clicked = True
        if Config.pause_other_audio and not unpause_needed:
            if process_name := audio_playering_app_name():
                if process_name != "ffplay.exe":
                    unpause_needed = True

    if e.event_type == "down" and not Cosmic.on:
        # 标记最后按下的时间
        last_time_pressed = time.time()
        # 根據上一次是否短時間內(`is_short_duration`)按下錄音鍵,來判斷是否需要輸出 `簡/繁`
        if double_clicked and Config.enable_double_click_opposite_state:
            Cosmic.opposite_state = not Cosmic.opposite_state
        # 记录开始时间
        launch_task()

    if e.event_type == "up":
        # 标记最后弹起的时间
        last_time_released = time.time()
        # 记录持续时间，并标识录音线程停止向队列放数据
        duration = time.time() - last_time_pressed
        # 取消或停止任务
        if duration < Config.threshold and not double_clicked:
            hold_mode_first_time_cancel_task = True

            cancel_task()

        else:
            finish_task()
            # 松开快捷键后，再按一次，恢复 CapsLock 或 Shift 等按键的状态
            if not double_clicked and Config.restore_key:
                time.sleep(0.01)
                keyboard.send(Config.speech_recognition_shortcut)
            # 恢复輸出 `簡/繁` 原来的狀態
            if Config.enable_double_click_opposite_state:
                double_clicked = False



# ==================== 绑定 handler ===============================


def hold_handler(e: keyboard.KeyboardEvent) -> None:
    # 验证按键名正确
    if not shortcut_correct(e):
        return

    # 长按模式
    hold_mode(e)


def click_handler(e: keyboard.KeyboardEvent) -> None:
    global _debug_action_count
    _debug_action_count += 1
    event_time = getattr(e, "time", None)
    event_lag_ms = (
        max(0.0, (time.time() - float(event_time)) * 1000.0)
        if event_time
        else None
    )
    lag_text = f" lag={event_lag_ms:.1f}ms" if event_lag_ms is not None else ""
    _debug_log(
        f"event#{_debug_action_count} type={e.event_type} name={e.name}{lag_text} "
        f"key_pressed={key_pressed} double_clicked={double_clicked} "
        f"cosmic_on={bool(Cosmic.on)}",
        force=(event_lag_ms is not None and event_lag_ms >= _DEBUG_SLOW_MS),
    )
    # 验证按键名正确
    if not shortcut_correct(e):
        return

    # 单击模式
    click_mode(e)


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
    if Config.hold_mode:
        keyboard.hook_key(
            Config.speech_recognition_shortcut, hold_handler, suppress=Config.suppress
        )
    else:
        # 单击模式，必须得阻塞快捷键
        # 收到长按时，再模拟发送按键
        keyboard.hook_key(
            Config.speech_recognition_shortcut, click_handler, suppress=True
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
        keyboard.hook_key(toggle_shortcut, textbox_context_toggle_handler, suppress=False)
    except Exception as exc:
        console.print(f"绑定文本框上下文快捷键失败：{exc}", style="#ff5555")
