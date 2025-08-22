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


async def send_audio():
    try:
        # 生成唯一任务 ID
        task_id = str(uuid.uuid1())

        # 任务起始时间
        time_start = 0

        # 音频数据临时存放处
        cache = []
        all_data = []  # 用于在结束时组合为完整音频
        duration = 0

        # 保存音频文件
        file_path, file = "", None

        # 开始取数据
        # task: {'type', 'time', 'data'}
        while task := await Cosmic.queue_in.get():
            Cosmic.queue_in.task_done()
            if task["type"] == "begin":
                time_start = task["time"]
            elif task["type"] == "data":
                # 在阈值之前积攒音频数据
                if task["time"] - time_start < Config.threshold:
                    cache.append(task["data"])
                    continue

                # 创建音频文件
                if Config.save_audio and not file_path:
                    file_path, file = create_file(task["data"].shape[1], time_start)
                    Cosmic.audio_files[task_id] = file_path

                # 获取音频数据
                if cache:
                    data = np.concatenate(cache)
                    cache.clear()
                else:
                    data = task["data"]

                # 记录用于上传的原始数据
                all_data.append(data.copy())

                # 保存音频至本地文件
                duration += len(data) / 48000
                if Config.save_audio:
                    write_file(file, data)
            elif task["type"] == "finish":
                # 记录收到 finish 消息（键抬起后）的时间点
                record_stop = task.get("time", time.time())
                t_finish_entry = time.time()
                # 完成写入本地文件
                if Config.save_audio:
                    finish_file(file)

                console.print(f"任务标识：{task_id}")
                console.print(f"    录音时长：{duration:.2f}s")

                # 结束时，将累计的数据转为 WAV 临时文件并上传到 OpenAI 兼容端点
                if all_data:
                    audio_concat = np.concatenate(all_data)
                else:
                    audio_concat = np.zeros((0, 1), dtype=np.float32)

                # 保证 shape 为 (n, channels)
                if audio_concat.ndim == 1:
                    audio_concat = audio_concat.reshape(-1, 1)

                # 可选：先转为单声道以减小体积
                if _force_mono() and audio_concat.shape[1] > 1:
                    audio_concat = audio_concat.mean(axis=1, keepdims=True)

                # 可选：降采样（48000 -> 16000），减少 3 倍体积
                target_sr = _get_target_sample_rate()
                if target_sr not in (48000, 44100, 32000, 24000, 16000):
                    target_sr = 48000
                if target_sr != 48000 and (48000 % target_sr == 0):
                    step = 48000 // target_sr
                    audio_concat = audio_concat[::step, :]
                actual_sr = target_sr if target_sr in (48000, 44100, 32000, 24000, 16000) else 48000

                # 构造内存中的 WAV（48000Hz, 16-bit PCM），避免磁盘 I/O
                # 编码为 MP3（优先）或 WAV（回退）
                pcm = (audio_concat * (2 ** 15 - 1)).astype(np.int16).tobytes()
                payload_buf: io.BytesIO
                payload_mime: str
                t_prep_start = time.time()
                encode_ms = 0.0
                if _use_mp3_upload():
                    try:
                        ffmpeg = shutil.which("ffmpeg")
                        args = [
                            ffmpeg,
                            "-hide_banner",
                            "-loglevel",
                            "error",
                            "-f",
                            "s16le",
                            "-ar",
                            str(actual_sr),
                            "-ac",
                            str(audio_concat.shape[1]),
                            "-i",
                            "pipe:0",
                            "-vn",
                            "-c:a",
                            "libmp3lame",
                            "-b:a",
                            _mp3_bitrate(),
                            "-f",
                            "mp3",
                            "pipe:1",
                        ]
                        flags = sp.CREATE_NO_WINDOW if hasattr(sp, "CREATE_NO_WINDOW") else 0
                        proc = await asp.create_subprocess_exec(
                            *args,
                            stdin=PIPE,
                            stdout=PIPE,
                            stderr=PIPE,
                            creationflags=flags,
                        )
                        stdout, stderr = await proc.communicate(input=pcm)
                        if proc.returncode == 0 and stdout:
                            payload_buf = io.BytesIO(stdout)
                            payload_mime = "audio/mpeg"
                            encode_ms = (time.time() - t_prep_start) * 1000.0
                        else:
                            # 回退 WAV
                            wav_buf = io.BytesIO()
                            with wave.open(wav_buf, "wb") as wf:
                                wf.setnchannels(audio_concat.shape[1])
                                wf.setsampwidth(2)
                                wf.setframerate(actual_sr)
                                wf.writeframes(pcm)
                            wav_buf.seek(0)
                            payload_buf = wav_buf
                            payload_mime = "audio/wav"
                            encode_ms = (time.time() - t_prep_start) * 1000.0
                    except Exception:
                        # 回退 WAV
                        wav_buf = io.BytesIO()
                        with wave.open(wav_buf, "wb") as wf:
                            wf.setnchannels(audio_concat.shape[1])
                            wf.setsampwidth(2)
                            wf.setframerate(actual_sr)
                            wf.writeframes(pcm)
                        wav_buf.seek(0)
                        payload_buf = wav_buf
                        payload_mime = "audio/wav"
                        encode_ms = (time.time() - t_prep_start) * 1000.0
                else:
                    # 直接 WAV
                    wav_buf = io.BytesIO()
                    with wave.open(wav_buf, "wb") as wf:
                        wf.setnchannels(audio_concat.shape[1])
                        wf.setsampwidth(2)
                        wf.setframerate(actual_sr)
                        wf.writeframes(pcm)
                    wav_buf.seek(0)
                    payload_buf = wav_buf
                    payload_mime = "audio/wav"
                    encode_ms = (time.time() - t_prep_start) * 1000.0

                # 上传（带重试）
                api_base = _get_api_base()
                url = f"{api_base}/v1/audio/transcriptions"
                # 统一在全局客户端中设置 headers
                data_form_base = {
                    "model": _get_model(),
                    "prompt": _get_prompt(),
                    "response_format": os.getenv("OPENAI_TRANSCRIBE_FORMAT", "text"),
                    "language": _get_language(),
                }
                max_retries = int(os.getenv("OPENAI_TRANSCRIBE_RETRIES", "3"))
                base_delay = float(os.getenv("OPENAI_TRANSCRIBE_BACKOFF_BASE", "0.5"))
                enable_stream_pref = _streaming_enabled()

                # 精确记录提交与完成的时间，供延时统计
                t_presubmit = time.time()
                fname = "mic.mp3" if payload_mime == "audio/mpeg" else "mic.wav"
                status_code = 0
                text_result = ""
                t_submit = t_presubmit
                t_complete = t_presubmit

                for attempt in range(max_retries):
                    # 每次尝试都需重置文件指针
                    try:
                        payload_buf.seek(0)
                    except Exception:
                        pass

                    # 尝试顺序：
                    #   1) 按用户偏好：stream + http2 (使用全局客户端)
                    #   2) stream + http1 (一次性客户端)
                    #   3) 非流式 + http1 (一次性客户端)
                    attempt_stream = enable_stream_pref if attempt == 0 else (enable_stream_pref and (attempt == 1))
                    use_http2 = _HTTP2_ENABLED if attempt == 0 else False

                    data_form = dict(data_form_base)
                    if attempt_stream:
                        data_form["stream"] = "true"

                    files = {"file": (fname, payload_buf, payload_mime)}

                    # 选择客户端
                    if attempt == 0:
                        client = await _get_http_client()
                        close_client = False  # persistent global client
                    else:
                        # 临时客户端（HTTP/1.1）避免污染全局
                        headers = _build_headers()
                        limits = _build_limits()
                        client = httpx.AsyncClient(timeout=httpx.Timeout(120.0), headers=headers, http2=use_http2, limits=limits)
                        close_client = True

                    err_text = None
                    try:
                        t_submit = time.time()
                        if attempt_stream:
                            # 以流式读取响应（SSE/分行 JSON）
                            current_text = ""
                            last_emit = 0.0
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
                                        # SSE keep-alive
                                        continue
                                    if s.startswith("data:"):
                                        s = s[5:].strip()
                                    if s in ("[DONE]", "DONE"):
                                        # 结束
                                        break
                                    # 尝试解析 JSON 行；若失败就当作纯文本累加
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
                                                # OpenAI-like stream payload with choices[0].delta.content
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
                                        # 非 JSON，按纯文本处理
                                        current_text += s
                                        new_text = current_text

                                    # 节流：每 50ms 推一次，且文本有增长
                                    now = time.time()
                                    if new_text is not None and (now - last_emit >= 0.05) and len(new_text) > 0:
                                        last_emit = now
                                        await Cosmic.queue_out.put(
                                            {
                                                "task_id": task_id,
                                                "is_final": False,
                                                "text": new_text,
                                                "time_start": time_start,
                                                "time_stop": record_stop,
                                                "time_submit": t_submit,
                                                "time_complete": now,
                                                "source": "mic",
                                                "stream": True,
                                            }
                                        )

                                # 最终结果
                                t_complete = time.time()
                                text_result = current_text
                        else:
                            resp = await client.post(url, data=data_form, files=files)
                            t_complete = time.time()
                            status_code = resp.status_code
                            if resp.status_code >= 500 or resp.status_code in (408, 429):
                                # 可重试状态码
                                err_text = resp.text
                                raise httpx.HTTPStatusError("服务暂时不可用", request=resp.request, response=resp)
                            if resp.status_code >= 400:
                                # 不可重试，直接失败
                                console.print(f"服务响应错误：{resp.status_code} {resp.text}", style="bright_red")
                                text_result = ""
                            else:
                                text_result = resp.text
                                if (
                                    len(text_result) >= 2 and text_result.startswith("\"") and text_result.endswith("\"")
                                ):
                                    text_result = text_result[1:-1]

                        # 成功，跳出重试循环
                        if close_client:
                            try:
                                await client.aclose()
                            except Exception:
                                pass
                        break
                    except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.ConnectError, httpx.RemoteProtocolError, httpx.HTTPError, OSError) as e:
                        # 连接中断/HTTP2 终止/超时等，执行指数退避重试
                        t_complete = time.time()
                        msg = err_text or str(e)
                        console.print(
                            f"网络异常（第 {attempt + 1}/{max_retries} 次）：{msg} | http2={use_http2} | stream={attempt_stream}",
                            style="bright_yellow",
                        )
                        # 如果是使用持久化客户端（attempt==0），认为连接已不可用，关闭以触发下次重建
                        if attempt == 0 and not close_client:
                            try:
                                await _close_http_client(reason=f"error:{e.__class__.__name__}")
                            except Exception:
                                pass
                        if close_client:
                            try:
                                await client.aclose()
                            except Exception:
                                pass
                        if attempt + 1 >= max_retries:
                            # 最终失败
                            console.print("已达到最大重试次数，返回当前结果（可能为空）", style="bright_red")
                            # 保持 text_result 为当前已累计的内容（若有）
                            break
                        # 延时后重试
                        delay = base_delay * (2 ** attempt) + random.uniform(0.0, 0.1)
                        await asyncio.sleep(delay)

                # 可选的调试输出
                wav_ms = encode_ms  # 兼容旧字段名，始终定义，供后续使用
                if os.getenv("CAPSWRITER_DEBUG_TIMING"):
                    pre_submit_ms = (t_submit - t_presubmit) * 1000.0
                    queue_delay_ms = (t_finish_entry - record_stop) * 1000.0
                    upload_s = (t_complete - t_submit)
                    total_s = (t_complete - record_stop)
                    wav_bytes = payload_buf.getbuffer().nbytes
                    console.print(
                        f"    [debug] 阶段: 队列等待 {queue_delay_ms:.0f}ms | 编码 {wav_ms:.0f}ms | 准备发送 {pre_submit_ms:.0f}ms | 上传+服务 {upload_s:.2f}s | 自抬键总计 {total_s:.2f}s | 大小 {wav_bytes/1024:.1f}KB @ {actual_sr}Hz/{audio_concat.shape[1]}ch [{payload_mime}] | http2={_HTTP2_ENABLED}",
                        style="dim",
                    )

                # 将结果放入客户端结果队列，由 recv_result 统一处理输出/翻译/打字
                message = {
                    "task_id": task_id,
                    "is_final": True,
                    "text": text_result,
                    "time_start": time_start,           # 开始录音（墙钟）
                    "time_stop": record_stop,           # 抬键时间（墙钟）
                    "time_submit": t_submit,            # 提交到后端（墙钟）
                    "time_complete": t_complete,        # 收到结果（墙钟）
                    "source": "mic",
                    "stream": _streaming_enabled(),
                    "debug_timing": {
                        "queue_delay_ms": max(0.0, (t_finish_entry - record_stop) * 1000.0),
                        "wav_ms": max(0.0, wav_ms),
                        "pre_submit_ms": max(0.0, (t_submit - t_presubmit) * 1000.0),
                        "upload_s": max(0.0, (t_complete - t_submit)),
                        "total_since_keyup_s": max(0.0, (t_complete - record_stop)),
                        "wav_bytes": payload_buf.getbuffer().nbytes,
                        "sr": actual_sr,
                        "channels": int(audio_concat.shape[1]),
                        "record_duration_s": float(duration),
                        "record_duration_by_key_s": max(0.0, record_stop - time_start),
                        "http_status": int(status_code),
                        "mime": payload_mime,
                        "bitrate": _mp3_bitrate() if payload_mime == "audio/mpeg" else None,
                        "http2": _HTTP2_ENABLED,
                    },
                }
                await Cosmic.queue_out.put(message)
                break
            elif task["type"] == "cancel":
                # 告诉服务端任务已取消
                # 仍然向结果队列投递一个空结果，确保 UI 和状态能恢复
                message = {
                    "task_id": task_id,
                    "is_final": True,
                    "text": "",
                    "time_start": time_start,
                    "time_submit": time.time(),
                    "time_complete": time.time(),
                    "source": "mic",
                }
                await Cosmic.queue_out.put(message)
                break
    except Exception as e:
        console.print(e)
