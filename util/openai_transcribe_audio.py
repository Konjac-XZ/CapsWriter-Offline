import io
import os
import time
import wave
import shutil
from asyncio.subprocess import PIPE
import asyncio.subprocess as asp
import subprocess as sp

import numpy as np


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


def _ffmpeg_path() -> str | None:
    return shutil.which("ffmpeg")


def _ffmpeg_processing_enabled() -> bool:
    # Whether to let ffmpeg handle resampling and mono fold-down when available
    return os.getenv("OPENAI_TRANSCRIBE_FFMPEG_PROCESS", "1").strip() not in ("0", "false", "False")


def _prefer_ffmpeg_for_wav() -> bool:
    # If true, use ffmpeg for WAV generation too (for better resample/mix); fallback to Python WAV if ffmpeg fails
    return os.getenv("OPENAI_TRANSCRIBE_FFMPEG_WAV", "1").strip() not in ("0", "false", "False")


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


async def make_audio_payload(audio_concat: np.ndarray, actual_sr: int) -> tuple[io.BytesIO, str, float, int, int]:
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


def preprocess_audio(audio_concat: np.ndarray) -> tuple[np.ndarray, int]:
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


# Public helpers for orchestrator/debug
def get_mp3_bitrate() -> str:
    return _mp3_bitrate()

