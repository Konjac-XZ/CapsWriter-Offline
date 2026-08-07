import asyncio
import os
import time
import warnings

import pangu
from src.audio.rename_audio import rename_audio
from src.infra.config import config as Config
from src.infra.cosmic import Cosmic, console
from src.infra.daily_input_stats import record_input_characters
from src.infra.gui_output import gui_event
from src.pipeline.regex_replace import regex_replace
from src.pipeline.type_result import type_result
from src.pipeline.write_md import write_md
from src.polish.llm_polish import (
    is_llm_polish_enabled,
    polish_text,
    record_finalized_text,
    should_polish_text,
)
from src.tsf_ipc import get_tsf_speech_tip_bridge
from src.tsf_ipc.protocol import CompositionStyle

warnings.filterwarnings("ignore")


def _emit_status_overlay(action: str, state: str | None = None) -> None:
    if not Config.show_listening_overlay:
        return
    try:
        payload = {"action": action}
        if state:
            payload["state"] = state
        gui_event("status_overlay", **payload)
    except Exception:
        pass


def _clear_abandoned_task(task_id: str | None) -> None:
    if task_id is not None:
        Cosmic.abandoned_task_ids.discard(task_id)
        if getattr(Cosmic, "active_task_id", None) == task_id:
            Cosmic.active_task_id = None
    Cosmic.abandon_requested = False


def _clear_active_task(task_id: str | None) -> None:
    if task_id is not None and getattr(Cosmic, "active_task_id", None) == task_id:
        Cosmic.active_task_id = None


def _is_abandoned(task_id: str | None) -> bool:
    return Cosmic.abandon_requested or (
        task_id is not None and task_id in Cosmic.abandoned_task_ids
    )


async def _cancel_abandoned_tsf_task(tsf_bridge, task_id: str | None) -> None:
    """Cancel the composition before forgetting an abandoned task."""
    try:
        await tsf_bridge.cancel(task_id)
    finally:
        _clear_abandoned_task(task_id)
        _clear_active_task(task_id)


def _transcription_delay(message: dict) -> float:
    """Return ASR latency after recording stops, excluding recording time."""
    time_complete = message.get("time_complete", 0)
    time_stop = message.get("time_stop")
    time_submit = message.get("time_submit", 0)
    delay_start = time_stop if time_stop is not None else time_submit
    delay = time_complete - delay_start
    if delay < 0 or delay > 600:
        # 防御性：若时间源混乱，避免显示荒谬时延
        delay = max(0.0, delay)
    return delay


async def recv_result():
    # 直接从本地结果队列读取（由 send_audio 推送），不再依赖远程 websocket
    current_tid = None
    tsf_bridge = get_tsf_speech_tip_bridge()
    try:
        while True:
            message = await Cosmic.queue_out.get()
            Cosmic.queue_out.task_done()
            text = message.get("text", "")
            raw_asr = text  # ← 保存 ASR 接口返回的原始文本（润色前）
            delay = _transcription_delay(message)

            # 基本标记
            is_final = bool(message.get("is_final"))
            is_transcript_delta = bool(message.get("is_transcript_delta", False))
            is_full_text_revision = (
                message.get("transcript_revision_mode") == "full_text"
            )
            has_incremental_transcript = bool(
                message.get("has_incremental_transcript", message.get("stream", False))
            )
            hide_status_overlay_when_done = is_final
            current_tid = message.get("task_id")
            if current_tid is not None:
                current_tid = str(current_tid)
            polish_prefetch_task = message.get("polish_prefetch_task")
            polish_enabled_for_text = False
            if current_tid is not None:
                Cosmic.active_task_id = current_tid
            if _is_abandoned(current_tid):
                if (
                    isinstance(polish_prefetch_task, asyncio.Task)
                    and not polish_prefetch_task.done()
                ):
                    polish_prefetch_task.cancel()
                await _cancel_abandoned_tsf_task(tsf_bridge, current_tid)
                if hide_status_overlay_when_done:
                    _emit_status_overlay("hide")
                continue

            # 最终结果可选走一次 LLM 润色；保持在正则替换与空白格式化之前
            if is_final:
                console.print(f"转录原文：{raw_asr}", soft_wrap=True)
                _t_polish = time.monotonic()
                polish_enabled_for_text = should_polish_text(text)
                if polish_enabled_for_text:
                    _emit_status_overlay("show", "polishing")
                if current_tid is not None and is_llm_polish_enabled():
                    # Entering the polishing phase does not mean that polished
                    # text exists yet. Keep the final ASR hypothesis dashed
                    # while waiting for the provider's first output token.
                    await tsf_bridge.begin_or_revise(
                        current_tid,
                        text,
                        CompositionStyle.TRANSCRIPTION,
                    )

                async def on_polished_text(revised_text: str) -> None:
                    if current_tid is not None and tsf_bridge.owns_task(current_tid):
                        await tsf_bridge.begin_or_revise(
                            current_tid,
                            revised_text,
                            CompositionStyle.POLISHING,
                        )

                polish_task = asyncio.create_task(
                    polish_text(
                        text,
                        on_text=on_polished_text if polish_enabled_for_text else None,
                        prepared_context=polish_prefetch_task,
                    )
                )
                Cosmic.active_polish_task = polish_task
                try:
                    text = await polish_task
                except asyncio.CancelledError:
                    if _is_abandoned(current_tid):
                        await _cancel_abandoned_tsf_task(tsf_bridge, current_tid)
                        _emit_status_overlay("hide")
                        continue
                    raise
                finally:
                    if getattr(Cosmic, "active_polish_task", None) is polish_task:
                        Cosmic.active_polish_task = None
                _polish_elapsed = time.monotonic() - _t_polish
            else:
                _polish_elapsed = 0.0

            if _is_abandoned(current_tid):
                await _cancel_abandoned_tsf_task(tsf_bridge, current_tid)
                if hide_status_overlay_when_done:
                    _emit_status_overlay("hide")
                continue

            # 正则替换（在 LLM 润色之后、pangu 之前执行）
            text = regex_replace(text)

            # # 中文数字 ITN（在正则替换之后、空白格式化之前执行）
            # text = chinese_to_num(text)

            # 仅在最终结果时进行音频重命名与 Markdown 写入
            file_audio = None
            if is_final:
                if Config.save_audio and message.get("task_id") in Cosmic.audio_files:
                    # 重命名录音文件
                    file_audio = rename_audio(
                        message.get("task_id"), text, message.get("time_start")
                    )
                if Config.save_markdown:
                    # 记录写入 md 文件
                    write_md(text, message.get("time_start"), file_audio)

            # 控制台输出
            if is_final:
                # 使用 pangu 对完整文本进行中英文混排空格优化，仅用于显示/输出
                text = pangu.spacing_text(text)
                if _is_abandoned(current_tid):
                    await _cancel_abandoned_tsf_task(tsf_bridge, current_tid)
                    _emit_status_overlay("hide")
                    continue
                # 若末尾不是有效标点，则补中文句号
                console.print(f"识别结果：{text}", soft_wrap=True)
                console.print(
                    f"总时延：{delay + _polish_elapsed:.2f}s = {delay:.2f}s + {_polish_elapsed:.2f}s"
                )
                dbg = message.get("debug_timing")
                if os.getenv("CAPSWRITER_DEBUG_TIMING"):
                    console.print(
                        (
                            f" 阶段: 队列等待 {dbg.get('queue_delay_ms', 0):.0f}ms | "
                            f"WAV {dbg.get('wav_ms', 0):.0f}ms | 准备发送 {dbg.get('pre_submit_ms', 0):.0f}ms | "
                            f"上传+服务 {dbg.get('upload_s', 0):.2f}s | 自抬键总计 {dbg.get('total_since_keyup_s', 0):.2f}s | "
                            f"大小 {dbg.get('wav_bytes', 0) / 1024:.1f}KB @ {dbg.get('sr')}Hz/{dbg.get('channels')}ch"
                        ),
                        style="dim",
                    )
                console.line()
            elif os.getenv("CAPSWRITER_DEBUG_TRANSCRIPT_DELTA"):
                # Opt-in diagnostics only. Realtime providers send cumulative
                # full-text hypotheses, so logging each event can copy and
                # render the entire growing transcript repeatedly.
                try:
                    last_len_dbg = getattr(Cosmic, "_last_transcript_delta_len", 0)
                    inc_dbg = text[last_len_dbg:]
                    if inc_dbg:
                        console.print(f"[transcript-delta] +{inc_dbg}", style="dim")
                except Exception:
                    pass

            # 打字：增量转录结果用"模拟键入"，最终结果才使用剪贴板粘贴（以减少光标跳动）
            async def type_transcript_delta(s: str):
                import keyboard as _kb

                # 仅增量字符，避免重复：比较上次输出长度
                last_len = getattr(Cosmic, "_last_transcript_delta_len", 0)
                inc = s[last_len:]
                if inc:
                    _kb.write(inc)
                    record_input_characters(
                        inc, log_interval=Config.daily_input_log_interval
                    )
                    Cosmic._last_transcript_delta_len = last_len + len(inc)
                    # 标记本 task 曾有增量转录结果输出
                    Cosmic._transcript_had_deltas = True

            async def type_final(s: str):
                # 重置流长度计数器
                if hasattr(Cosmic, "_last_transcript_delta_len"):
                    Cosmic._last_transcript_delta_len = 0
                await type_result(s)
                record_input_characters(s, log_interval=Config.daily_input_log_interval)

            # 每个 task 的增量转录结果独立计数，切换 task 时重置（避免跨任务污染）
            last_tid = getattr(Cosmic, "_last_transcript_delta_task", None)
            if current_tid != last_tid:
                Cosmic._last_transcript_delta_task = current_tid
                if hasattr(Cosmic, "_last_transcript_delta_len"):
                    Cosmic._last_transcript_delta_len = 0
                # 新任务开始时，清理增量输出标记
                Cosmic._transcript_had_deltas = False

            final_output_succeeded = not is_final
            if is_transcript_delta:
                # 润色开启时，优先由 TSF Composition 承载可修订的 full text。
                # BEGIN 未获得前台 TIP 的 APPLIED ACK 时保留旧的键入回退。
                tsf_owned = False
                llm_polish_enabled = is_llm_polish_enabled()
                if current_tid is not None and llm_polish_enabled:
                    tsf_owned = await tsf_bridge.begin_or_revise(
                        current_tid,
                        text,
                        CompositionStyle.TRANSCRIPTION,
                    )
                if (
                    not tsf_owned
                    and not tsf_bridge.owns_task(current_tid)
                    and not is_full_text_revision
                    and not llm_polish_enabled
                ):
                    # A full-text hypothesis may revise its unstable suffix.
                    # The legacy keyboard fallback can only append, so suppress
                    # intermediate updates and let the final result paste once.
                    await type_transcript_delta(text)
            else:
                # 最终：按原逻辑输出，但走剪贴板粘贴
                # 若此前已有增量转录结果输出，则不再进行最终粘贴，避免重复
                if current_tid is not None and tsf_bridge.owns_task(current_tid):
                    if await tsf_bridge.commit(current_tid, text):
                        # TSF 的流式 revision 只是在更新 composition；等最终
                        # 后处理文本提交成功后再一次性计入，避免重复统计。
                        record_input_characters(
                            text, log_interval=Config.daily_input_log_interval
                        )
                        final_output_succeeded = True
                    elif await tsf_bridge.cancel(current_tid):
                        # A confirmed CANCEL proves that the uncommitted
                        # composition is gone, so the legacy paste cannot
                        # duplicate it. If cancellation is not confirmed, keep
                        # the bridge state for later cleanup and do not paste.
                        await type_final(text)
                        final_output_succeeded = True
                    else:
                        console.print(
                            "TSF 最终提交与取消均未确认，已抑制剪贴板回退以避免重复上屏。",
                            style="yellow",
                        )
                elif current_tid is not None and getattr(
                    tsf_bridge,
                    "take_confirmed_termination_rollback",
                    lambda _task_id: False,
                )(current_tid):
                    # The TIP erased the externally terminated partial
                    # composition using its write edit cookie. A complete
                    # final paste is now safe and cannot duplicate that text.
                    await type_final(text)
                    final_output_succeeded = True
                    console.print(
                        "TSF Composition 被宿主提前终止；未完成原文已回滚，"
                        "已使用完整最终文本安全回退。",
                        style="yellow",
                    )
                elif current_tid is not None and getattr(
                    tsf_bridge,
                    "take_failed_termination_rollback",
                    lambda _task_id: False,
                )(current_tid):
                    # The host ended the composition but the TIP could not
                    # prove that all partial text was erased. Pasting would
                    # risk duplicating or corrupting user text.
                    console.print(
                        "TSF Composition 被宿主提前终止，且无法确认未完成原文已完整回滚；"
                        "已抑制最终文本回退以避免重复上屏。",
                        style="yellow",
                    )
                elif has_incremental_transcript and getattr(
                    Cosmic, "_transcript_had_deltas", False
                ):
                    # 完结时重置计数与标记
                    if hasattr(Cosmic, "_last_transcript_delta_len"):
                        Cosmic._last_transcript_delta_len = 0
                    Cosmic._transcript_had_deltas = False
                    # 不进行任何粘贴输出
                    final_output_succeeded = True
                else:
                    await type_final(text)
                    final_output_succeeded = True
            if is_final and text and final_output_succeeded:
                from src.keyboard.play_music import play_completion_sound

                # 只记录已经确认上屏的最终文本；同时更新内存与持久化历史。
                tracked_session_id = getattr(
                    tsf_bridge, "take_committed_session_id", lambda _task_id: None
                )(current_tid)
                if tracked_session_id is None:
                    record_finalized_text(text)
                else:
                    record_finalized_text(text, tracked_session_id)
                play_completion_sound()
            _clear_active_task(current_tid)
            if hide_status_overlay_when_done:
                _emit_status_overlay("hide")
    except Exception as e:
        _emit_status_overlay("hide")
        print(e)
    finally:
        if tsf_bridge.owns_task(current_tid):
            await tsf_bridge.cancel(current_tid)
        _clear_active_task(current_tid)


if __name__ == "__main__":
    pass
