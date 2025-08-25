import asyncio
import io
import json
import os
import time
import uuid
import shutil

import httpx
import numpy as np
import random
import atexit

from util.client_cosmic import Cosmic, console
from util.client_create_file import create_file
from util.client_finish_file import finish_file
from util.client_write_file import write_file
from util.config import ClientConfig as Config

# New modules for clearer separation of concerns
from util.openai_transcribe_audio import preprocess_audio
from util.openai_transcribe_audio import make_audio_payload
from util.openai_transcribe_audio import get_mp3_bitrate
from util.transcribe_provider import transcribe_audio, get_stream_flag


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
            if Config.save_audio:
                write_file(file, data)
        elif ttype == "finish":
            record_stop = task.get("time", time.time())
            t_finish_entry = time.time()
            if Config.save_audio:
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

        # Upload with retries via provider abstraction
        max_retries = int(os.getenv("OPENAI_TRANSCRIBE_RETRIES", "3"))
        base_delay = float(os.getenv("OPENAI_TRANSCRIBE_BACKOFF_BASE", "0.05"))
        enable_stream_pref = get_stream_flag()

        t_presubmit = time.time()
        text_result, status_code, t_submit, t_complete, transport_info = await transcribe_audio(
            payload_buf, payload_mime, task_id, time_start, record_stop, max_retries, base_delay
        )

        # Optional debug
        wav_ms = encode_ms
        if os.getenv("CAPSWRITER_DEBUG_TIMING"):
            pre_submit_ms = (t_submit - t_presubmit) * 1000.0
            queue_delay_ms = (t_finish_entry - record_stop) * 1000.0
            upload_s = (t_complete - t_submit)
            total_s = (t_complete - record_stop)
            wav_bytes = payload_buf.getbuffer().nbytes
            http2_flag = transport_info.get("http2") if isinstance(transport_info, dict) else None
            console.print(
                f"[debug] 阶段: 队列等待 {queue_delay_ms:.0f}ms | 编码 {wav_ms:.0f}ms | 准备发送 {pre_submit_ms:.0f}ms | 上传+服务 {upload_s:.2f}s | 自抬键总计 {total_s:.2f}s | 大小 {wav_bytes/1024:.1f}KB @ {payload_sr}Hz/{payload_ch}ch [{payload_mime}] | http2={http2_flag}",
                style="dim",
            )

        # Emit final
        message = {
            "task_id": task_id,
            "is_final": True,
            "text": text_result,
            "time_start": time_start,
            "time_stop": record_stop,
            "time_submit": t_submit,
            "time_complete": t_complete,
            "source": "mic",
            "stream": get_stream_flag(),
            "debug_timing": {
                "queue_delay_ms": max(0.0, (t_finish_entry - record_stop) * 1000.0),
                "wav_ms": max(0.0, wav_ms),
                "pre_submit_ms": max(0.0, (t_submit - t_presubmit) * 1000.0),
                "upload_s": max(0.0, (t_complete - t_submit)),
                "total_since_keyup_s": max(0.0, (t_complete - record_stop)),
                "wav_bytes": payload_buf.getbuffer().nbytes,
                "sr": int(payload_sr),
                "channels": int(payload_ch),
                "record_duration_s": float(duration),
                "record_duration_by_key_s": max(0.0, record_stop - time_start),
                "http_status": int(status_code),
                "mime": payload_mime,
                "bitrate": get_mp3_bitrate() if payload_mime == "audio/mpeg" else None,
                "http2": (transport_info.get("http2") if isinstance(transport_info, dict) else None),
            },
        }
        await Cosmic.queue_out.put(message)
    except Exception as e:
        console.print(e)
