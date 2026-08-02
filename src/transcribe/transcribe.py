import asyncio
import os
import re
import time
from pathlib import Path

import httpx
from src.infra.response_parse import extract_text_from_body
from src.infra.cosmic import Cosmic, console
from src.provider.provider_config import provider_manager


def _get_api_base() -> str:
    return (os.getenv("OPENAI_BASE_URL") or "").rstrip("/")


def _get_api_key() -> str:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        # Fail fast: require the API key to be provided via environment variable
        raise RuntimeError(
            "OPENAI_API_KEY environment variable is required but not set"
        )
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
        console.print(
            "未检测到 OPENAI_API_KEY 环境变量，使用内置示例密钥进行个人测试。",
            style="yellow",
        )


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

    # Respect active OpenAI provider's omit_response_format flag if available
    omit_rf = False
    try:
        active = provider_manager.get_active_provider()
        if active and isinstance(active.settings, dict):
            omit_rf = bool(active.settings.get("openai_omit_response_format", False))
    except Exception:
        omit_rf = False

    data = {
        "model": model,
        # Get prompt from provider configuration
        "prompt": provider_manager.get_provider_prompt(),
        "language": os.getenv("OPENAI_TRANSCRIBE_LANGUAGE", "zh"),
    }
    if not omit_rf:
        data["response_format"] = os.getenv("OPENAI_TRANSCRIBE_FORMAT", "text")

    mime = _get_mime_type(file)
    time_start = time.time()
    console.print(f"\n任务开始：上传并转录 -> {file}")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            with open(file, "rb") as f:
                files = {"file": (file.name, f, mime)}
                resp = await client.post(url, headers=headers, data=data, files=files)
        if resp.status_code >= 400:
            console.print(
                f"服务响应错误：{resp.status_code} {resp.text}", style="bright_red"
            )
            # 标记失败结果，便于 recv 侧退出
            Cosmic.audio_files[str(file)] = {
                "ok": False,
                "error": resp.text,
                "time_start": time_start,
                "time_complete": time.time(),
            }
            return

        # 解析正文：支持纯文本、JSON 对象、或带标签的 JSON 行（如 "识别结果：{...}"）
        body = resp.text
        parsed = None
        try:
            parsed = extract_text_from_body(body)
        except Exception:
            parsed = None
        text_result = (
            parsed if isinstance(parsed, str) and parsed.strip() != "" else body
        )
        # 某些服务会返回带引号的纯文本，尽量去除首尾引号（若存在）
        if (
            len(text_result) >= 2
            and text_result.startswith('"')
            and text_result.endswith('"')
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
        console.print(
            f"\033[K    处理失败（{process_duration:.2f}s）：{message.get('error')}",
            style="bright_red",
        )
        return

    # 解析结果（OpenAI 兼容端点使用纯文本返回）
    text_merge = message.get("text", "").strip()
    text_split = re.sub(r"([，。？.!?])", r"\1\n", text_merge)
    # 文件名
    txt_filename = Path(file).with_suffix(".txt")
    merge_filename = Path(file).with_suffix(".merge.txt")

    # 写入结果
    with open(merge_filename, "w", encoding="utf-8") as f:
        f.write(text_merge)
    with open(txt_filename, "w", encoding="utf-8") as f:
        f.write(text_split)

    process_duration = message["time_complete"] - message["time_start"]
    console.print(f"\033[K    处理耗时：{process_duration:.2f}s")
    console.print(f"    识别结果：\n[green]{text_merge}")
