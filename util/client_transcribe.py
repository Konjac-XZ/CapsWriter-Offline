import asyncio
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
from util.client_cosmic import Cosmic, console
from util.client_hot_sub import hot_sub
from util.client_hot_update import observe_hot, update_hot_all


def _get_api_base() -> str:
    return os.getenv("OPENAI_BASE_URL").rstrip("/")


def _get_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Fail fast: require the API key to be provided via environment variable
        raise RuntimeError("OPENAI_API_KEY environment variable is required but not set")
    return api_key

def _get_model() -> str:
    return os.getenv("TRANSCRIBE_MODEL", "gpt-4o-transcribe")


def _get_mime_type(file: Path) -> str:
    suffix = file.suffix.lower()
    return {
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".m4a": "audio/mp4",
        ".flac": "audio/flac",
        ".ogg": "audio/ogg",
        ".aac": "audio/aac",
        ".wma": "audio/x-ms-wma",
        ".amr": "audio/amr",
    }.get(suffix, "application/octet-stream")


async def transcribe_check(file: Path):
    # 仅检查文件是否存在；HTTP 端点无需预先握手
    if not file.exists():
        console.print(f"文件不存在：{file}", style="bright_red")
        return False
    # 简要提示 API 配置（未设置环境变量则使用内置示例）
    if not os.getenv("OPENAI_API_KEY"):
        console.print("未检测到 OPENAI_API_KEY 环境变量，使用内置示例密钥进行个人测试。", style="yellow")


async def transcribe_send(file: Path):
    # 通过 OpenAI 兼容端点一次性上传文件并获取结果
    api_base = _get_api_base()
    api_key = _get_api_key()
    model = _get_model()
    url = f"{api_base}/v1/audio/transcriptions"

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        # 不要手动设置 Content-Type，httpx 会自动处理 multipart 边界
    }

    data = {
        "model": model,
        # 复用用户提供的示例提示词，可按需通过环境变量覆盖 TRANSCRIBE_PROMPT
        "prompt": os.getenv(
            "TRANSCRIBE_PROMPT",
            (
            """
            The user's primary occupation is as a computer systems security engineer.
            Recently, he has been using Ghidra to write the source code for his next research paper.
            Consider terminology relevant to this field.
            
            Spaces should be added between Chinese and ASCII characters according to certain rules. Carefully handle the spacing issues in mixed Chinese and English text based on the rules provided below. 
  
            - Chinese characters and numbers: require a space. (e.g. 2025 年)
            - Chinese characters and English words: require a space. (e.g. A/B 测试)
            - Numbers and units: require a space, except for % and °. (e.g. 10 kg, 20%, 360°)
            - Full-width Chinese punctuation and any character: do not require a space. (e.g. “你好，世界！”)
            - Hyphens and slashes, backslashes: Do not add spaces. (e.g. 10-20, 10/20, 10\20)
            """
            ),
        ),
        "response_format": os.getenv("OPENAI_TRANSCRIBE_FORMAT", "text"),
        "language": os.getenv("OPENAI_TRANSCRIBE_LANGUAGE", "zh"),
    }

    mime = _get_mime_type(file)
    time_start = time.time()
    console.print(f"\n任务开始：上传并转录 -> {file}")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0)) as client:
            with open(file, "rb") as f:
                files = {"file": (file.name, f, mime)}
                resp = await client.post(url, headers=headers, data=data, files=files)
        if resp.status_code >= 400:
            console.print(f"服务响应错误：{resp.status_code} {resp.text}", style="bright_red")
            # 标记失败结果，便于 recv 侧退出
            Cosmic.audio_files[str(file)] = {
                "ok": False,
                "error": resp.text,
                "time_start": time_start,
                "time_complete": time.time(),
            }
            return

        text_result = resp.text
        # 某些服务会返回带引号的纯文本，尽量去除首尾引号（若存在）
        if (
            len(text_result) >= 2
            and text_result.startswith("\"")
            and text_result.endswith("\"")
        ):
            text_result = text_result[1:-1]

        Cosmic.audio_files[str(file)] = {
            "ok": True,
            "text": text_result,
            "time_start": time_start,
            "time_complete": time.time(),
        }
    except Exception as e:
        Cosmic.audio_files[str(file)] = {
            "ok": False,
            "error": str(e),
            "time_start": time_start,
            "time_complete": time.time(),
        }
        console.print(f"上传或转录失败：{e}", style="bright_red")


async def transcribe_recv(file: Path):
    # 更新热词并监听动态变化（与旧实现保持一致）
    update_hot_all()
    observer = observe_hot()

    # 轮询等待发送侧填充结果（保持与 core_client.py 的并发结构兼容）
    key = str(file)
    while key not in Cosmic.audio_files:
        console.print("    等待服务端结果...", end="\r")
        await asyncio.sleep(0.2)

    message = Cosmic.audio_files.pop(key)
    if not message.get("ok"):
        process_duration = message.get("time_complete", time.time()) - message.get(
            "time_start", time.time()
        )
        console.print(f"\033[K    处理失败（{process_duration:.2f}s）：{message.get('error')}", style="bright_red")
        return

    # 解析结果（OpenAI 兼容端点使用纯文本返回）
    text_merge = message.get("text", "").strip()
    # 热词替换
    text_merge = hot_sub(text_merge)
    # 将中英常见句末标点换行，便于生成 srt
    text_split = re.sub(r"([，。？.!?])", r"\1\n", text_merge)
    # 文件名
    txt_filename = Path(file).with_suffix(".txt")
    merge_filename = Path(file).with_suffix(".merge.txt")

    # 写入结果
    with open(merge_filename, "w", encoding="utf-8") as f:
        f.write(text_merge)
    with open(txt_filename, "w", encoding="utf-8") as f:
        f.write(text_split)
    # 已禁用字幕（srt）生成功能

    process_duration = message["time_complete"] - message["time_start"]
    console.print(f"\033[K    处理耗时：{process_duration:.2f}s")
    console.print(f"    识别结果：\n[green]{text_merge}")
