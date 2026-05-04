import os
import io
import time
import uuid

import numpy as np

from src.infra.cosmic import Cosmic, console
from src.audio.create_file import create_file
from src.audio.finish_file import finish_file
from src.audio.retry_cache import get_latest_audio_path, mime_for_path, write_retry_cache
from src.audio.write_file import write_file
from src.infra.config import ClientConfig as Config
from src.infra.gui_output import gui_event

# New modules for clearer separation of concerns
from src.transcribe.openai.openai_transcribe_audio import preprocess_audio
from src.transcribe.openai.openai_transcribe_audio import make_audio_payload
from src.transcribe.openai.openai_transcribe_audio import get_mp3_bitrate
from src.transcribe.api import transcribe_audio, get_stream_flag


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


def _payload_bytes(payload_buf) -> bytes:
    if hasattr(payload_buf, "getvalue"):
        return payload_buf.getvalue()
    if hasattr(payload_buf, "tell") and hasattr(payload_buf, "seek") and hasattr(payload_buf, "read"):
        pos = payload_buf.tell()
        payload_buf.seek(0)
        data = payload_buf.read()
        payload_buf.seek(pos)
        return data
    return bytes(payload_buf)


async def _submit_payload(
    *,
    payload_buf,
    payload_mime: str,
    task_id: str,
    time_start: float,
    record_stop: float,
    t_finish_entry: float,
    encode_ms: float,
    payload_sr: int | None,
    payload_ch: int | None,
    duration: float,
    source: str,
    cache_retry_audio: bool,
) -> None:
    if hasattr(payload_buf, "seek"):
        payload_buf.seek(0)
    payload_bytes = _payload_bytes(payload_buf)

    if cache_retry_audio and payload_bytes:
        try:
            write_retry_cache(
                payload_bytes,
                payload_mime,
                {
                    "source_task_id": task_id,
                    "record_duration_s": float(duration),
                    "time_start": time_start,
                    "time_stop": record_stop,
                },
            )
        except Exception as exc:
            console.print(f"保存重试录音缓存失败：{exc}", style="bright_red")

    max_retries = int(os.getenv("OPENAI_TRANSCRIBE_RETRIES", "3"))
    base_delay = float(os.getenv("OPENAI_TRANSCRIBE_BACKOFF_BASE", "0.05"))

    t_presubmit = time.time()
    upload_buf = io.BytesIO(payload_bytes)
    text_result, status_code, t_submit, t_complete, transport_info = await transcribe_audio(
        upload_buf, payload_mime, task_id, time_start, record_stop, max_retries, base_delay
    )

    if os.getenv("CAPSWRITER_DEBUG_TIMING"):
        pre_submit_ms = (t_submit - t_presubmit) * 1000.0
        queue_delay_ms = (t_finish_entry - record_stop) * 1000.0
        upload_s = t_complete - t_submit
        total_s = t_complete - record_stop
        http2_flag = transport_info.get("http2") if isinstance(transport_info, dict) else None
        console.print(
            f"[debug] 阶段: 队列等待 {queue_delay_ms:.0f}ms | 编码 {encode_ms:.0f}ms | 准备发送 {pre_submit_ms:.0f}ms | 上传+服务 {upload_s:.2f}s | 自抬键总计 {total_s:.2f}s | 大小 {len(payload_bytes)/1024:.1f}KB @ {payload_sr}Hz/{payload_ch}ch [{payload_mime}] | http2={http2_flag}",
            style="dim",
        )

    message = {
        "task_id": task_id,
        "is_final": True,
        "text": text_result,
        "time_start": time_start,
        "time_stop": record_stop,
        "time_submit": t_submit,
        "time_complete": t_complete,
        "source": source,
        "stream": get_stream_flag(),
        "debug_timing": {
            "queue_delay_ms": max(0.0, (t_finish_entry - record_stop) * 1000.0),
            "wav_ms": max(0.0, encode_ms),
            "pre_submit_ms": max(0.0, (t_submit - t_presubmit) * 1000.0),
            "upload_s": max(0.0, (t_complete - t_submit)),
            "total_since_keyup_s": max(0.0, (t_complete - record_stop)),
            "wav_bytes": len(payload_bytes),
            "sr": int(payload_sr or 0),
            "channels": int(payload_ch or 0),
            "record_duration_s": float(duration),
            "record_duration_by_key_s": max(0.0, record_stop - time_start),
            "http_status": int(status_code),
            "mime": payload_mime,
            "bitrate": get_mp3_bitrate() if payload_mime == "audio/mpeg" else None,
            "http2": (transport_info.get("http2") if isinstance(transport_info, dict) else None),
        },
    }
    await Cosmic.queue_out.put(message)


async def _gather_audio_once(task_id: str) -> tuple[np.ndarray, float, float, float, float, str | None]:
    """Read from queue until finish or cancel, write file if enabled, and assemble audio.

    Returns (audio_concat, duration, time_start, record_stop, t_finish_entry, cancel_reason). If canceled, audio is empty and
    record_stop may be current time, and cancel_reason is non-None ("cancel").
    """
    time_start = 0.0
    cache: list[np.ndarray] = []
    all_data: list[np.ndarray] = []
    duration = 0.0
    file_path, file = "", None

    while task := await Cosmic.queue_in.get():
        Cosmic.queue_in.task_done()
        ttype = task.get("type")
        if ttype == "begin":
            time_start = task["time"]
        elif ttype == "data":
            if task["time"] - time_start < Config.threshold:
                cache.append(task["data"])
                continue
            if Config.save_audio and not file_path:
                file_path, file = create_file(task["data"].shape[1], time_start)
                Cosmic.audio_files[task_id] = file_path
            if cache:
                data = np.concatenate(cache)
                cache.clear()
            else:
                data = task["data"]
            all_data.append(data.copy())
            duration += len(data) / 48000
            if Config.save_audio and file is not None:
                write_file(file, data)
        elif ttype == "finish":
            record_stop = task.get("time", time.time())
            t_finish_entry = time.time()
            if Config.save_audio and file is not None:
                finish_file(file)
            if all_data:
                audio_concat = np.concatenate(all_data)
            else:
                audio_concat = np.zeros((0, 1), dtype=np.float32)
            return audio_concat, duration, time_start, record_stop, t_finish_entry, None
        elif ttype == "cancel":
            # no audio to upload; caller will emit blank result
            now = time.time()
            return np.zeros((0, 1), dtype=np.float32), duration, time_start, now, now, "cancel"

    # Shouldn't reach here normally
    now = time.time()
    return np.zeros((0, 1), dtype=np.float32), duration, time_start, now, now, "cancel"


async def send_audio():
    try:
        Cosmic.transcribe_busy = True
        task_id = str(uuid.uuid1())
        # Gather audio once (until finish or cancel)
        audio_concat, duration, time_start, record_stop, t_finish_entry, cancel_reason = await _gather_audio_once(task_id)

        if cancel_reason is not None:
            # Emit a final empty result to restore UI state
            await Cosmic.queue_out.put(
                {
                    "task_id": task_id,
                    "is_final": True,
                    "text": "",
                    "time_start": time_start,
                    "time_submit": time.time(),
                    "time_complete": time.time(),
                    "source": "mic",
                }
            )
            return

        # Log identifiers
        console.print(f"录音时长：{duration:.2f}s")

        # Preprocess audio (mono/downsample)
        audio_proc, actual_sr = preprocess_audio(audio_concat)

        # Build payload
        payload_buf, payload_mime, encode_ms, payload_sr, payload_ch = await make_audio_payload(audio_proc, actual_sr)

        await _submit_payload(
            payload_buf=payload_buf,
            payload_mime=payload_mime,
            task_id=task_id,
            time_start=time_start,
            record_stop=record_stop,
            t_finish_entry=t_finish_entry,
            encode_ms=encode_ms,
            payload_sr=payload_sr,
            payload_ch=payload_ch,
            duration=duration,
            source="mic",
            cache_retry_audio=True,
        )
    except Exception as e:
        _emit_status_overlay("hide")
        console.print(e)
    finally:
        Cosmic.transcribe_busy = False


async def retry_latest_audio() -> None:
    task_id = str(uuid.uuid1())
    latest_path = get_latest_audio_path()
    if latest_path is None:
        console.print("没有可重试的最近录音。", style="bright_yellow")
        return

    try:
        Cosmic.transcribe_busy = True
        payload_bytes = latest_path.read_bytes()
        if not payload_bytes:
            console.print("最近录音缓存为空，无法重试。", style="bright_yellow")
            return

        now = time.time()
        payload_mime = mime_for_path(latest_path)
        console.print(f"重试最近请求：{latest_path.name}")
        await _submit_payload(
            payload_buf=io.BytesIO(payload_bytes),
            payload_mime=payload_mime,
            task_id=task_id,
            time_start=now,
            record_stop=now,
            t_finish_entry=now,
            encode_ms=0.0,
            payload_sr=None,
            payload_ch=None,
            duration=0.0,
            source="retry",
            cache_retry_audio=False,
        )
    except Exception as e:
        console.print(f"重试最近请求失败：{e}", style="bright_red")
    finally:
        Cosmic.transcribe_busy = False
