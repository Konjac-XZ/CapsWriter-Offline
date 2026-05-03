from __future__ import annotations

import asyncio
import base64
import ctypes
import json
import os
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from src.infra.cosmic import Cosmic, console
from src.infra.response_parse import extract_text_from_body

try:
    import win32con
    import win32gui
    import win32process
    import win32ui
except Exception:  # pragma: no cover - runtime environment specific
    win32con = None
    win32gui = None
    win32process = None
    win32ui = None


@dataclass(slots=True)
class ActiveWindowCapture:
    hwnd: int
    title: str
    class_name: str | None
    process_id: int | None
    process_name: str | None
    width: int
    height: int
    image_bytes: bytes
    mime_type: str = "image/png"


_missing_config_warned = False
_feature_state_logged = False
_vision_config_cache: dict[str, Any] = {}
_vision_config_mtime: float | None = None


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

def _get_root_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent.parent


def _load_vision_config() -> dict:
    config_path = _get_root_dir() / "config" / "polish" / "vision.yaml"
    global _vision_config_cache, _vision_config_mtime

    try:
        mtime = config_path.stat().st_mtime
    except FileNotFoundError:
        _vision_config_cache = {}
        _vision_config_mtime = None
        console.print(
            f"[vision_context] 配置文件未找到：{config_path}，功能默认关闭。",
            style="yellow",
        )
        return {}
    except Exception:
        _vision_config_cache = {}
        _vision_config_mtime = None
        console.print(
            f"[vision_context] 配置文件状态读取失败：{config_path}，功能默认关闭。",
            style="yellow",
        )
        return {}

    if _vision_config_mtime == mtime:
        return _vision_config_cache

    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        _vision_config_cache = {}
        _vision_config_mtime = mtime
        console.print(
            f"[vision_context] 配置文件加载失败：{exc}，功能默认关闭。",
            style="yellow",
        )
        return {}

    _vision_config_cache = data if isinstance(data, dict) else {}
    _vision_config_mtime = mtime
    return _vision_config_cache


def _cfg() -> dict:
    return _load_vision_config()


# ---------------------------------------------------------------------------
# Env helpers
# ---------------------------------------------------------------------------

def _get_env(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value else default


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_vision_context_enabled() -> bool:
    return bool(_cfg().get("enabled", False))


def start_vision_context_service() -> asyncio.Task | None:
    if platform.system() != "Windows":
        console.print("[vision_context] 当前平台不是 Windows，已跳过。", style="yellow")
        return None

    loop = asyncio.get_running_loop()
    task = loop.create_task(_vision_context_loop(), name="vision_context_loop")
    Cosmic.vision_context_task = task
    return task


async def stop_vision_context_service(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        Cosmic.vision_context_task = None


def get_recent_vision_context_summary() -> str | None:
    if not is_vision_context_enabled():
        return None

    payload = getattr(Cosmic, "vision_context", None)
    if not isinstance(payload, dict):
        return None

    summary = str(payload.get("summary") or "").strip()
    if not summary:
        return None

    interval = max(10.0, float(_cfg().get("interval_seconds", 60.0)))
    ttl = max(interval * 2.0, float(_cfg().get("summary_ttl_seconds", interval * 2.0)))
    captured_at = float(payload.get("captured_at") or 0.0)
    if captured_at and time.time() - captured_at > ttl:
        return None

    max_chars = max(64, int(_cfg().get("summary_max_chars", 800)))
    if len(summary) > max_chars:
        summary = summary[: max_chars - 1].rstrip() + "…"

    meta: list[str] = []
    process_name = str(payload.get("process_name") or "").strip()
    title = str(payload.get("title") or "").strip()
    if process_name:
        meta.append(f"进程：{process_name}")
    if title:
        meta.append(f"窗口标题：{title}")

    if meta:
        return "\n".join(["；".join(meta), f"视觉摘要：{summary}"])
    return summary


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------

async def _vision_context_loop() -> None:
    global _feature_state_logged

    if not _feature_state_logged:
        if is_vision_context_enabled():
            console.print("[vision_context] 功能已启用。", style="dim")
        _feature_state_logged = True

    if not is_vision_context_enabled():
        return

    interval_s = max(10.0, float(_cfg().get("interval_seconds", 60.0)))
    while True:
        try:
            await _refresh_vision_context_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            Cosmic.vision_context_last_error = f"{type(exc).__name__}: {exc}"
            console.print(
                f"[vision_context] 后台更新异常：{type(exc).__name__}: {exc}",
                style="yellow",
            )
        await asyncio.sleep(interval_s)


async def _refresh_vision_context_once() -> None:
    global _missing_config_warned

    cfg = _cfg()
    base_url = _get_env("LLM_VISION_BASE_URL")
    api_key = _get_env("LLM_VISION_API_KEY")
    model: str | None = cfg.get("model") or None
    timeout_s = float(cfg.get("timeout", 30.0))

    if not base_url or not api_key or not model:
        if not _missing_config_warned:
            missing = [
                label
                for label, value in (
                    ("LLM_VISION_BASE_URL (env)", base_url),
                    ("LLM_VISION_API_KEY (env)", api_key),
                    ("model (config/polish/vision.yaml)", model),
                )
                if not value
            ]
            console.print(
                f"[vision_context] 配置不完整，跳过视觉摘要。缺少：{', '.join(missing)}",
                style="yellow",
            )
            _missing_config_warned = True
        return

    _missing_config_warned = False

    capture = await asyncio.to_thread(_capture_active_window)
    if capture is None:
        console.print("[vision_context] 未能捕获当前活动窗口，保留上次视觉摘要。", style="dim")
        return

    console.print(
        (
            "[vision_context] 已捕获活动窗口"
            f" hwnd={capture.hwnd} size={capture.width}x{capture.height}"
            f" title={capture.title or 'unknown'}"
        ),
        style="dim",
    )

    summary = await _request_vision_summary(capture, base_url, api_key, model, timeout_s)
    if not summary:
        return

    Cosmic.vision_context = {
        "captured_at": time.time(),
        "summary": summary,
        "title": capture.title,
        "class_name": capture.class_name,
        "process_id": capture.process_id,
        "process_name": capture.process_name,
        "width": capture.width,
        "height": capture.height,
        "hwnd": capture.hwnd,
    }
    Cosmic.vision_context_last_error = None
    console.print(
        f"[vision_context] 视觉摘要已更新，长度={len(summary)}",
        style="dim",
    )


# ---------------------------------------------------------------------------
# Vision request
# ---------------------------------------------------------------------------

def _build_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/chat"):
        return f"{base}/completions"
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _extract_error_message(payload: Any) -> str | None:
    if not isinstance(payload, dict):
        return None
    err = payload.get("error")
    if isinstance(err, dict):
        for key in ("message", "code", "type"):
            value = err.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    if isinstance(err, str) and err.strip():
        return err.strip()
    return None


async def _request_vision_summary(
    capture: ActiveWindowCapture,
    base_url: str,
    api_key: str,
    model: str,
    timeout_s: float,
) -> str | None:
    cfg = _cfg()
    prompt = str(cfg.get("prompt") or "").strip()
    detail = str(cfg.get("image_detail") or "auto").strip() or "auto"
    temperature = cfg.get("temperature")
    max_output_tokens = cfg.get("max_output_tokens")

    data_url = _to_data_url(capture.image_bytes, capture.mime_type)
    user_text = _build_capture_prompt(capture)
    messages: list[dict[str, Any]] = []
    if prompt:
        messages.append(
            {
                "role": "system",
                "content": prompt,
            }
        )
    messages.append(
        {
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": data_url,
                        "detail": detail,
                    },
                },
            ],
        }
    )

    body: dict[str, Any] = {
        "model": model,
        "stream": False,
        "messages": messages,
    }
    if temperature is not None:
        body["temperature"] = float(temperature)
    if max_output_tokens is not None:
        body["max_tokens"] = int(max_output_tokens)

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    url = _build_url(base_url)

    console.print(
        (
            f"[vision_context] 发送视觉摘要请求 -> {url}"
            f" model={model} image_bytes={len(capture.image_bytes)}"
        ),
        style="dim",
    )

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_s)) as client:
            response = await client.post(url, headers=headers, json=body)

        if response.status_code >= 400:
            detail_text = None
            try:
                detail_text = _extract_error_message(response.json())
            except Exception:
                detail_text = response.text.strip() or None
            Cosmic.vision_context_last_error = f"HTTP {response.status_code}: {detail_text or ''}".strip()
            console.print(
                f"[vision_context] 视觉摘要请求失败：{response.status_code} {detail_text or ''}".rstrip(),
                style="yellow",
            )
            return None

        try:
            payload = response.json()
            body_text = json.dumps(payload, ensure_ascii=False)
        except Exception:
            body_text = response.text

        summary = extract_text_from_body(body_text)
        if isinstance(summary, str) and summary.strip():
            return summary.strip()

        console.print(
            f"[vision_context] 响应未提取到摘要，原始正文（前 400 字符）：{body_text[:400]}",
            style="yellow",
        )
        return None
    except httpx.TimeoutException as exc:
        Cosmic.vision_context_last_error = f"timeout: {exc}"
        console.print(
            f"[vision_context] 视觉摘要请求超时（timeout={timeout_s}s）：{exc}",
            style="yellow",
        )
        return None
    except Exception as exc:
        Cosmic.vision_context_last_error = f"{type(exc).__name__}: {exc}"
        console.print(
            f"[vision_context] 视觉摘要异常：{type(exc).__name__}: {exc}",
            style="yellow",
        )
        return None


# ---------------------------------------------------------------------------
# Active-window capture
# ---------------------------------------------------------------------------

def _capture_active_window() -> ActiveWindowCapture | None:
    if platform.system() != "Windows":
        return None
    if not all((win32gui, win32ui, win32process, win32con)):
        return None

    try:
        hwnd = int(win32gui.GetForegroundWindow() or 0)
    except Exception:
        return None

    if not hwnd:
        return None

    try:
        if not win32gui.IsWindow(hwnd) or not win32gui.IsWindowVisible(hwnd):
            return None
        if win32gui.IsIconic(hwnd):
            return None
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
    except Exception:
        return None

    width = max(0, int(right - left))
    height = max(0, int(bottom - top))
    if width < 32 or height < 32:
        return None

    image_bytes = _capture_window_png(hwnd, width, height)
    if not image_bytes:
        return None

    process_id = None
    try:
        process_id = int(win32process.GetWindowThreadProcessId(hwnd)[1])
    except Exception:
        process_id = None

    return ActiveWindowCapture(
        hwnd=hwnd,
        title=_safe_window_title(hwnd),
        class_name=_safe_class_name(hwnd),
        process_id=process_id,
        process_name=_safe_process_name(process_id),
        width=width,
        height=height,
        image_bytes=image_bytes,
    )


def _safe_window_title(hwnd: int) -> str:
    try:
        return win32gui.GetWindowText(hwnd) or ""
    except Exception:
        return ""


def _safe_class_name(hwnd: int) -> str | None:
    try:
        class_name = win32gui.GetClassName(hwnd)
    except Exception:
        return None
    return class_name or None


def _safe_process_name(process_id: int | None) -> str | None:
    if not process_id:
        return None

    try:
        import psutil

        return psutil.Process(process_id).name()
    except Exception:
        return None


def _capture_window_png(hwnd: int, width: int, height: int) -> bytes | None:
    hwnd_dc = None
    src_dc = None
    mem_dc = None
    bitmap = None
    try:
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        if not hwnd_dc:
            return None

        src_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        mem_dc = src_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(src_dc, width, height)
        mem_dc.SelectObject(bitmap)

        render_full_content_flag = 0x00000002
        result = ctypes.windll.user32.PrintWindow(hwnd, mem_dc.GetSafeHdc(), render_full_content_flag)
        if result != 1:
            mem_dc.BitBlt((0, 0), (width, height), src_dc, (0, 0), win32con.SRCCOPY)

        bmp_info = bitmap.GetInfo()
        bmp_bytes = bitmap.GetBitmapBits(True)
        return _bitmap_to_png(
            bmp_bytes,
            int(bmp_info.get("bmWidth", width)),
            int(bmp_info.get("bmHeight", height)),
            int(bmp_info.get("bmWidthBytes", width * 4)),
        )
    except Exception:
        return None
    finally:
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        try:
            if mem_dc is not None:
                mem_dc.DeleteDC()
        except Exception:
            pass
        try:
            if src_dc is not None:
                src_dc.DeleteDC()
        except Exception:
            pass
        try:
            if hwnd_dc is not None:
                win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            pass


def _bitmap_to_png(bitmap_bytes: bytes, width: int, height: int, bytes_per_line: int) -> bytes | None:
    if not bitmap_bytes or width <= 0 or height <= 0 or bytes_per_line <= 0:
        return None

    image = QImage(bitmap_bytes, width, height, bytes_per_line, QImage.Format.Format_ARGB32)
    if image.isNull():
        return None

    image = image.copy().mirrored(False, True)
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        return None
    if not image.save(buffer, b"PNG"):
        return None
    return bytes(buffer.data())


# ---------------------------------------------------------------------------
# Prompt helpers
# ---------------------------------------------------------------------------

def _to_data_url(image_bytes: bytes, mime_type: str) -> str:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _build_capture_prompt(capture: ActiveWindowCapture) -> str:
    lines = [
        "请根据这张当前活动窗口截图，总结用户此刻正在进行的工作。",
        "重点关注：当前软件/页面类型、正在编辑或查看的内容、明显的任务意图、可见关键词。",
        "避免臆测不可见内容；若看不清，请明确说明。输出简洁中文摘要。",
        f"窗口标题：{capture.title or '未知'}",
        f"窗口类名：{capture.class_name or '未知'}",
        f"进程名：{capture.process_name or '未知'}",
        f"窗口尺寸：{capture.width}x{capture.height}",
    ]
    return "\n".join(lines)
