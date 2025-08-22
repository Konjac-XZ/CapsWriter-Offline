import asyncio
import io
import json
import os
import time
import uuid
import wave
import shutil
from asyncio.subprocess import PIPE
import asyncio.subprocess as asp

import httpx
import numpy as np
import subprocess as sp
import random
import atexit

from util.client_cosmic import Cosmic, console
from util.client_create_file import create_file
from util.client_finish_file import finish_file
from util.client_write_file import write_file
from util.config import ClientConfig as Config


def _get_api_base() -> str:
    return os.getenv("OPENAI_BASE_URL", "https://api2.aigcbest.top").rstrip("/")


def _get_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Fail fast: require the API key to be provided via environment variable
        raise RuntimeError("OPENAI_API_KEY environment variable is required but not set")
    return api_key


def _get_model() -> str:
    return os.getenv("OPENAI_TRANSCRIBE_MODEL", "gpt-4o-transcribe")


def _get_prompt() -> str:
    return os.getenv(
        "OPENAI_TRANSCRIBE_PROMPT"
    )


def _get_language() -> str:
    return os.getenv("OPENAI_TRANSCRIBE_LANGUAGE", "zh")


def _get_target_sample_rate() -> int:
    try:
        return int(os.getenv("OPENAI_TRANSCRIBE_SAMPLE_RATE", "44100"))
    except Exception:
        return 48000


def _force_mono() -> bool:
    return os.getenv("OPENAI_TRANSCRIBE_MONO", "1").strip() not in ("0", "false", "False")


def _use_mp3_upload() -> bool:
    # 默认开启 MP3（若系统存在 ffmpeg），可通过环境变量关闭
    if shutil.which("ffmpeg") is None:
        return False
    return os.getenv("OPENAI_TRANSCRIBE_USE_MP3", "1").strip() not in ("0", "false", "False")


def _mp3_bitrate() -> str:
    return os.getenv("OPENAI_TRANSCRIBE_MP3_BITRATE", "64k")


def _streaming_enabled() -> bool:
    return os.getenv("OPENAI_TRANSCRIBE_STREAM", "1").strip() not in ("0", "false", "False")


def _ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def _ffmpeg_processing_enabled() -> bool:
    # Whether to let ffmpeg handle resampling and mono fold-down when available
    return os.getenv("OPENAI_TRANSCRIBE_FFMPEG_PROCESS", "1").strip() not in ("0", "false", "False")


def _prefer_ffmpeg_for_wav() -> bool:
    # If true, use ffmpeg for WAV generation too (for better resample/mix); fallback to Python WAV if ffmpeg fails
    return os.getenv("OPENAI_TRANSCRIBE_FFMPEG_WAV", "1").strip() not in ("0", "false", "False")


# 全局可复用 HTTP 客户端，启用 keep-alive/可选 HTTP/2，减少重复握手
_HTTP_CLIENT: httpx.AsyncClient | None = None
_HTTP2_ENABLED: bool = False
_CLIENT_GEN: int = 0


def _build_limits() -> httpx.Limits:
    keepalive_expiry = float(os.getenv("OPENAI_KEEPALIVE_EXPIRY", "90"))
    return httpx.Limits(
        max_keepalive_connections=5,
        max_connections=10,
        keepalive_expiry=keepalive_expiry,
    )


def _build_headers() -> dict:
    return {
        "Accept": "application/json",
        "Authorization": f"Bearer {_get_api_key()}",
    }


def _log_persistent_established():
    api_base = _get_api_base()
    console.print(
        f"[conn] Persistent HTTP client established | http2={_HTTP2_ENABLED} | base={api_base} | gen={_CLIENT_GEN}",
        style="cyan",
    )


def _log_persistent_closed(reason: str):
    console.print(
        f"[conn] Persistent HTTP client closed | reason={reason} | gen={_CLIENT_GEN}",
        style="cyan",
    )


async def _close_http_client(reason: str = "manual"):
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        return
    try:
        _log_persistent_closed(reason)
    except Exception:
        pass
    try:
        await _HTTP_CLIENT.aclose()
    except Exception:
        pass
    _HTTP_CLIENT = None


async def _get_http_client() -> httpx.AsyncClient:
    global _HTTP_CLIENT, _HTTP2_ENABLED, _CLIENT_GEN
    if _HTTP_CLIENT is not None:
        return _HTTP_CLIENT
    api_key = _get_api_key()
    headers = _build_headers()
    http2 = os.getenv("OPENAI_HTTP2", "1").strip() not in ("0", "false", "False")
    _HTTP2_ENABLED = http2
    limits = _build_limits()
    _HTTP_CLIENT = httpx.AsyncClient(
        timeout=httpx.Timeout(120.0), headers=headers, http2=http2, limits=limits
    )
    _CLIENT_GEN += 1
    _log_persistent_established()
    return _HTTP_CLIENT


def _atexit_close_client():
    # Best-effort close of the persistent AsyncClient on process exit.
    # This tries to avoid complaints if the loop is already closed.
    try:
        client = globals().get("_HTTP_CLIENT")
        if client is None:
            return
        loop = None
        try:
            loop = asyncio.get_event_loop()
        except Exception:
            loop = None
        # Log before attempting close
        try:
            _log_persistent_closed("process-exit")
        except Exception:
            pass
        if loop and loop.is_running():
            try:
                loop.create_task(client.aclose())
            except Exception:
                pass
        elif loop and not loop.is_closed():
            try:
                loop.run_until_complete(client.aclose())
            except Exception:
                pass
    except Exception:
        pass


# Register best-effort cleanup
atexit.register(_atexit_close_client)


def _build_wav_buf_from_pcm(pcm: bytes, channels: int, sr: int) -> io.BytesIO:
    """Encode raw PCM (s16le) bytes into a WAV buffer and return a seeked BytesIO."""
    wav_buf = io.BytesIO()
    with wave.open(wav_buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm)
    wav_buf.seek(0)
    return wav_buf


async def _make_audio_payload(audio_concat: np.ndarray, actual_sr: int) -> tuple[io.BytesIO, str, float, int, int]:
    """Create upload payload from float32 audio [-1,1].

    Prefers ffmpeg with float32 input for resample + mono fold-down and encoding (MP3 or WAV).
    Falls back to Python WAV writer with safe clipping.

    Returns (buffer, mime, elapsed_ms, payload_sample_rate, payload_channels).
    """
    t_start = time.time()
    in_channels = int(audio_concat.shape[1]) if audio_concat.ndim == 2 else 1

    ffmpeg = _ffmpeg_path()
    target_sr = _get_target_sample_rate()
    want_mono = _force_mono()
    out_channels = 1 if want_mono else in_channels

    # If we can, let ffmpeg do both resampling and fold-down from float32
    prefer_ffmpeg = ffmpeg is not None and _ffmpeg_processing_enabled()

    if _use_mp3_upload() and ffmpeg is not None:
        try:
            # Prepare float32 little-endian stream for ffmpeg input
            # Sanitize first to avoid NaN/Inf propagating
            np.nan_to_num(audio_concat, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
            f32 = np.clip(audio_concat, -1.0, 1.0).astype(np.float32, copy=False).tobytes()
            args = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-f", "f32le",
                "-ar", str(48000),  # input stream SR (capture is 48k)
                "-ac", str(in_channels),
                "-i", "pipe:0",
                "-vn",
                "-ac", str(out_channels),
                "-ar", str(target_sr),
                "-c:a", "libmp3lame",
                "-b:a", _mp3_bitrate(),
                "-f", "mp3",
                "pipe:1",
            ]
            flags = sp.CREATE_NO_WINDOW if hasattr(sp, "CREATE_NO_WINDOW") else 0
            proc = await asp.create_subprocess_exec(
                *args, stdin=PIPE, stdout=PIPE, stderr=PIPE, creationflags=flags
            )
            stdout, stderr = await proc.communicate(input=f32)
            if proc.returncode == 0 and stdout:
                elapsed = (time.time() - t_start) * 1000.0
                return io.BytesIO(stdout), "audio/mpeg", elapsed, int(target_sr), int(out_channels)
            # fallthrough if encoder fails
        except Exception:
            pass

    # Try WAV via ffmpeg if preferred and available
    if ffmpeg is not None and prefer_ffmpeg and _prefer_ffmpeg_for_wav():
        try:
            np.nan_to_num(audio_concat, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
            f32 = np.clip(audio_concat, -1.0, 1.0).astype(np.float32, copy=False).tobytes()
            args = [
                ffmpeg,
                "-hide_banner",
                "-loglevel", "error",
                "-f", "f32le",
                "-ar", str(48000),
                "-ac", str(in_channels),
                "-i", "pipe:0",
                "-vn",
                "-ac", str(out_channels),
                "-ar", str(target_sr),
                "-c:a", "pcm_s16le",
                "-f", "wav",
                "pipe:1",
            ]
            flags = sp.CREATE_NO_WINDOW if hasattr(sp, "CREATE_NO_WINDOW") else 0
            proc = await asp.create_subprocess_exec(
                *args, stdin=PIPE, stdout=PIPE, stderr=PIPE, creationflags=flags
            )
            stdout, stderr = await proc.communicate(input=f32)
            if proc.returncode == 0 and stdout:
                elapsed = (time.time() - t_start) * 1000.0
                return io.BytesIO(stdout), "audio/wav", elapsed, int(target_sr), int(out_channels)
        except Exception:
            pass

    # Final fallback: write WAV in Python using s16le; do safe clipping and keep given SR/channels
    np.nan_to_num(audio_concat, copy=False, nan=0.0, posinf=1.0, neginf=-1.0)
    f32_clamped = np.clip(audio_concat, -1.0, 1.0)
    pcm = (f32_clamped * (2 ** 15 - 1)).astype(np.int16).tobytes()
    wav_buf = _build_wav_buf_from_pcm(pcm, in_channels, actual_sr)
    elapsed = (time.time() - t_start) * 1000.0
    return wav_buf, "audio/wav", elapsed, int(actual_sr), int(in_channels)


def _preprocess_audio(audio_concat: np.ndarray) -> tuple[np.ndarray, int]:
    """Apply optional mono downmix and downsampling.

    Returns processed audio and the actual sample rate used.
    """
    if audio_concat.ndim == 1:
        audio_concat = audio_concat.reshape(-1, 1)

    # If we'll let ffmpeg handle resampling and mono, avoid altering the signal here
    if _ffmpeg_path() is not None and _ffmpeg_processing_enabled():
        return audio_concat, 48000

    # Otherwise: light-weight safe processing in Python
    # Optional mono fold-down by average (utility-grade)
    if _force_mono() and audio_concat.shape[1] > 1:
        audio_concat = audio_concat.mean(axis=1, keepdims=True)

    # Gate decimation: only when exact integer ratio and small factors to reduce aliasing risk
    target_sr = _get_target_sample_rate()
    if target_sr not in (48000, 44100, 32000, 24000, 16000):
        target_sr = 48000
    if target_sr != 48000 and (48000 % target_sr == 0):
        step = 48000 // target_sr
        if step in (2, 3):
            audio_concat = audio_concat[::step, :]
            actual_sr = target_sr
            return audio_concat, actual_sr

    # Default: keep original SR to avoid bad resampling
    return audio_concat, 48000


async def _emit_partial_update(task_id: str, new_text: str, time_start: float, record_stop: float, t_submit: float):
    """Emit a partial transcription update to the outbound queue."""
    await Cosmic.queue_out.put(
        {
            "task_id": task_id,
            "is_final": False,
            "text": new_text,
            "time_start": time_start,
            "time_stop": record_stop,
            "time_submit": t_submit,
            "time_complete": time.time(),
            "source": "mic",
            "stream": True,
        }
    )


async def _sse_transcribe(
    client: httpx.AsyncClient,
    url: str,
    data_form: dict,
    files: dict,
    task_id: str,
    time_start: float,
    record_stop: float,
) -> tuple[str, int, float]:
    """Perform streaming transcription and emit partial updates.

    Returns (final_text, status_code, t_complete).
    """
    t_submit = time.time()
    current_text = ""
    last_emit = 0.0
    status_code = 0
    async with client.stream("POST", url, data=data_form, files=files) as resp:
        status_code = resp.status_code
        if status_code >= 400:
            body = await resp.aread()
            try:
                err_text = body.decode("utf-8", errors="ignore")
            except Exception:
                err_text = str(body)
            raise httpx.HTTPStatusError("非成功状态码", request=resp.request, response=resp)
        async for line in resp.aiter_lines():
            if not line:
                continue
            s = line.strip()
            if s.startswith(":"):
                continue
            if s.startswith("data:"):
                s = s[5:].strip()
            if s in ("[DONE]", "DONE"):
                break
            new_text = None
            try:
                obj = json.loads(s)
                if isinstance(obj, dict):
                    if "delta" in obj and isinstance(obj["delta"], str):
                        current_text += obj["delta"]
                        new_text = current_text
                    elif "text" in obj and isinstance(obj["text"], str):
                        current_text = obj["text"]
                        new_text = current_text
                    elif "choices" in obj:
                        try:
                            delta = obj["choices"][0]["delta"].get("content")
                            if isinstance(delta, str):
                                current_text += delta
                                new_text = current_text
                        except Exception:
                            pass
                elif isinstance(obj, str):
                    current_text = obj
                    new_text = current_text
            except Exception:
                current_text += s
                new_text = current_text

            now = time.time()
            if new_text is not None and (now - last_emit >= 0.05) and len(new_text) > 0:
                last_emit = now
                await _emit_partial_update(task_id, new_text, time_start, record_stop, t_submit)

    t_complete = time.time()
    return current_text, status_code, t_complete


async def _nonstream_transcribe(
    client: httpx.AsyncClient,
    url: str,
    data_form: dict,
    files: dict,
) -> tuple[str, int, float, str | None]:
    """Perform non-streaming transcription. Returns (text, status_code, t_complete, err_text)."""
    resp = await client.post(url, data=data_form, files=files)
    t_complete = time.time()
    status_code = resp.status_code
    if resp.status_code >= 500 or resp.status_code in (408, 429):
        return "", status_code, t_complete, resp.text
    if resp.status_code >= 400:
        console.print(f"服务响应错误：{resp.status_code} {resp.text}", style="bright_red")
        return "", status_code, t_complete, None
    text_result = resp.text
    if len(text_result) >= 2 and text_result.startswith("\"") and text_result.endswith("\""):
        text_result = text_result[1:-1]
    return text_result, status_code, t_complete, None


async def _transcribe_with_retries(
    payload_buf: io.BytesIO,
    payload_mime: str,
    data_form_base: dict,
    url: str,
    enable_stream_pref: bool,
    task_id: str,
    time_start: float,
    record_stop: float,
    max_retries: int,
    base_delay: float,
) -> tuple[str, int, float, float]:
    """Retry wrapper that recreates the persistent client before each retry.

    Returns (text_result, status_code, t_submit, t_complete).
    """
    fname = "mic.mp3" if payload_mime == "audio/mpeg" else "mic.wav"
    text_result = ""
    status_code = 0
    t_submit = time.time()
    t_complete = t_submit

    for attempt in range(max_retries):
        try:
            payload_buf.seek(0)
        except Exception:
            pass

        if attempt > 0:
            try:
                await _close_http_client(reason=f"retry-recreate:{attempt}")
            except Exception:
                pass

        client = await _get_http_client()

        attempt_stream = enable_stream_pref if attempt == 0 else (enable_stream_pref and (attempt == 1))
        data_form = dict(data_form_base)
        if attempt_stream:
            data_form["stream"] = "true"
        files = {"file": (fname, payload_buf, payload_mime)}

        err_text = None
        try:
            t_submit = time.time()
            if attempt_stream:
                text_result, status_code, t_complete = await _sse_transcribe(
                    client, url, data_form, files, task_id, time_start, record_stop
                )
            else:
                text_result, status_code, t_complete, err_text = await _nonstream_transcribe(
                    client, url, data_form, files
                )
                if status_code >= 500 or status_code in (408, 429):
                    raise httpx.HTTPStatusError("服务暂时不可用", request=None, response=None)
            break
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError, httpx.RemoteProtocolError, httpx.HTTPError, OSError) as e:
            t_complete = time.time()
            msg = err_text or str(e)
            console.print(
                f"网络异常（第 {attempt + 1}/{max_retries} 次）：{msg} | http2={_HTTP2_ENABLED} | stream={attempt_stream}",
                style="bright_yellow",
            )
            try:
                await _close_http_client(reason=f"error:{e.__class__.__name__}")
            except Exception:
                pass
            if attempt + 1 >= max_retries:
                console.print("已达到最大重试次数，返回当前结果（可能为空）", style="bright_red")
                break
            delay = base_delay * (2 ** attempt) + random.uniform(0.0, 0.1)
            await asyncio.sleep(delay)

    return text_result, status_code, t_submit, t_complete


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
        console.print(f"任务标识：{task_id}")
        console.print(f"    录音时长：{duration:.2f}s")

        # Preprocess audio (mono/downsample)
        audio_proc, actual_sr = _preprocess_audio(audio_concat)

        # Build payload
        payload_buf, payload_mime, encode_ms, payload_sr, payload_ch = await _make_audio_payload(audio_proc, actual_sr)

        # Upload with retries
        api_base = _get_api_base()
        url = f"{api_base}/v1/audio/transcriptions"
        data_form_base = {
            "model": _get_model(),
            "prompt": _get_prompt(),
            "response_format": os.getenv("OPENAI_TRANSCRIBE_FORMAT", "text"),
            "language": _get_language(),
        }
        max_retries = int(os.getenv("OPENAI_TRANSCRIBE_RETRIES", "3"))
        base_delay = float(os.getenv("OPENAI_TRANSCRIBE_BACKOFF_BASE", "0.5"))
        enable_stream_pref = _streaming_enabled()

        t_presubmit = time.time()
        text_result, status_code, t_submit, t_complete = await _transcribe_with_retries(
            payload_buf,
            payload_mime,
            data_form_base,
            url,
            enable_stream_pref,
            task_id,
            time_start,
            record_stop,
            max_retries,
            base_delay,
        )

        # Optional debug
        wav_ms = encode_ms
        if os.getenv("CAPSWRITER_DEBUG_TIMING"):
            pre_submit_ms = (t_submit - t_presubmit) * 1000.0
            queue_delay_ms = (t_finish_entry - record_stop) * 1000.0
            upload_s = (t_complete - t_submit)
            total_s = (t_complete - record_stop)
            wav_bytes = payload_buf.getbuffer().nbytes
            console.print(
                f"    [debug] 阶段: 队列等待 {queue_delay_ms:.0f}ms | 编码 {wav_ms:.0f}ms | 准备发送 {pre_submit_ms:.0f}ms | 上传+服务 {upload_s:.2f}s | 自抬键总计 {total_s:.2f}s | 大小 {wav_bytes/1024:.1f}KB @ {payload_sr}Hz/{payload_ch}ch [{payload_mime}] | http2={_HTTP2_ENABLED}",
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
            "stream": _streaming_enabled(),
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
                "bitrate": _mp3_bitrate() if payload_mime == "audio/mpeg" else None,
                "http2": _HTTP2_ENABLED,
            },
        }
        await Cosmic.queue_out.put(message)
    except Exception as e:
        console.print(e)
