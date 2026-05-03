import os
import time
import opencc
import pangu
from src.infra.cosmic import Cosmic, console
from src.polish.llm_polish import polish_text, record_finalized_text
from src.pipeline.regex_replace import regex_replace
from src.audio.rename_audio import rename_audio
from src.pipeline.strip_punc import strip_punc
from src.pipeline.type_result import type_result
from src.pipeline.write_md import write_md
from src.infra.config import ClientConfig as Config
import warnings

warnings.filterwarnings("ignore")


async def recv_result():
    # 直接从本地结果队列读取（由 send_audio 推送），不再依赖远程 websocket
    try:
        while True:
            message = await Cosmic.queue_out.get()
            Cosmic.queue_out.task_done()
            text = message.get("text", "")
            raw_asr = text  # ← 保存 ASR 接口返回的原始文本（润色前）
            delay = message.get("time_complete", 0) - message.get("time_submit", 0)
            if delay < 0 or delay > 600:
                # 防御性：若时间源混乱，避免显示荒谬时延
                delay = max(0.0, delay)

            # 基本标记
            is_final = bool(message.get("is_final"))
            is_stream = bool(message.get("stream"))

            # 流式时：对中间增量不做末尾标点剥离，避免抖动
            if not (is_stream and not is_final):
                text = strip_punc(text)

            # 最终结果可选走一次 LLM 润色；保持在正则替换与空白格式化之前
            if is_final:
                console.print(f"ASR 原文：{raw_asr}", soft_wrap=True)
                _t_polish = time.monotonic()
                text = await polish_text(text)
                _polish_elapsed = time.monotonic() - _t_polish
                console.print(f"润色时延：{_polish_elapsed:.2f}s", style="dim")

            # 正则替换（在 strip_punc 之后、pangu / opencc 之前执行）
            text = regex_replace(text)

            # # 中文数字 ITN（在正则替换之后、空白格式化之前执行）
            # text = chinese_to_num(text)

            # 简繁转换
            convert_to_traditional_chinese_done = False
            traditional_text = None
            if is_final:
                converter = opencc.OpenCC(Config.opencc_converter)
                traditional_text = converter.convert(text)
                convert_to_traditional_chinese_done = True

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
                    match Config.convert_to_traditional_chinese_main:
                        case "繁":
                            write_md(traditional_text or text, message.get("time_start"), file_audio)
                        case _:
                            write_md(text, message.get("time_start"), file_audio)

            # 控制台输出
            if is_final:
                # 使用 pangu 对完整文本进行中英文混排空格优化，仅用于显示/输出
                text = pangu.spacing_text(text)
                # 若末尾不是有效标点，则补中文句号
                # 将已完成的文本存入历史，供下一次 LLM 润色使用
                record_finalized_text(text)
                console.print(f"转录时延：{delay:.2f}s")
                dbg = message.get("debug_timing")
                if os.getenv("CAPSWRITER_DEBUG_TIMING"):
                    console.print(
                        (
                            f" 阶段: 队列等待 {dbg.get('queue_delay_ms', 0):.0f}ms | "
                            f"WAV {dbg.get('wav_ms', 0):.0f}ms | 准备发送 {dbg.get('pre_submit_ms', 0):.0f}ms | "
                            f"上传+服务 {dbg.get('upload_s', 0):.2f}s | 自抬键总计 {dbg.get('total_since_keyup_s', 0):.2f}s | "
                            f"大小 {dbg.get('wav_bytes', 0)/1024:.1f}KB @ {dbg.get('sr')}Hz/{dbg.get('channels')}ch"
                        ),
                        style="dim",
                    )
                console.print(f"识别结果：{text}", soft_wrap=True)
                console.line()
            else:
                # 轻量日志：帮助定位流式过程中是否有数据
                try:
                    last_len_dbg = getattr(Cosmic, "_last_stream_len", 0)
                    inc_dbg = text[last_len_dbg:]
                    if inc_dbg:
                        console.print(f"[stream] +{inc_dbg}", style="dim")
                except Exception:
                    pass

            # 打字：流式增量用"模拟键入"，最终结果才使用剪贴板粘贴（以减少光标跳动）
            async def type_incremental(s: str):
                import keyboard as _kb
                # 仅增量字符，避免重复：比较上次输出长度
                last_len = getattr(Cosmic, "_last_stream_len", 0)
                inc = s[last_len:]
                if inc:
                    _kb.write(inc)
                    Cosmic._last_stream_len = last_len + len(inc)
                    # 标记本 task 曾有流式增量输出
                    Cosmic._stream_had_increments = True

            async def type_final(s: str):
                # 重置流长度计数器
                if hasattr(Cosmic, "_last_stream_len"):
                    Cosmic._last_stream_len = 0
                await type_result(s)

            # 每个 task 流式独立计数，切换 task 时重置（避免跨任务污染）
            current_tid = message.get("task_id")
            last_tid = getattr(Cosmic, "_last_stream_task", None)
            if current_tid != last_tid:
                Cosmic._last_stream_task = current_tid
                if hasattr(Cosmic, "_last_stream_len"):
                    Cosmic._last_stream_len = 0
                # 新任务开始时，清理流式标记
                Cosmic._stream_had_increments = False

            if is_stream and not is_final:
                # 增量：不做简繁转换，直接键入原文增量
                await type_incremental(text)
            else:
                # 最终：按原逻辑输出（含简繁转换），但走剪贴板粘贴
                # 若此前已有流式增量输出，则不再进行最终粘贴，避免重复
                if is_stream and getattr(Cosmic, "_stream_had_increments", False):
                    # 完结时重置计数与标记
                    if hasattr(Cosmic, "_last_stream_len"):
                        Cosmic._last_stream_len = 0
                    Cosmic._stream_had_increments = False
                    # 不进行任何粘贴输出
                    pass
                elif convert_to_traditional_chinese_done:
                    match Config.convert_to_traditional_chinese_main:
                        case "繁":
                            if Cosmic.opposite_state:
                                # text 已在上方做过 pangu 与标点补全
                                await type_final(text)
                            else:
                                traditional_text = pangu.spacing_text(traditional_text)
                                await type_final(traditional_text)
                        case _:
                            if Cosmic.opposite_state:
                                traditional_text = pangu.spacing_text(traditional_text)
                                await type_final(traditional_text)
                            else:
                                # text 已在上方做过 pangu 与标点补全
                                await type_final(text)
                    convert_to_traditional_chinese_done = False
                else:
                    await type_final(text)
            Cosmic.opposite_state = False
    except Exception as e:
        print(e)
    finally:
        return


if __name__ == "__main__":
    None
