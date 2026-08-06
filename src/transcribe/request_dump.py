"""Opt-in request payload dumps shared by transcription providers."""

from __future__ import annotations

from datetime import datetime
import base64
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any

from src.infra.cosmic import console
from src.provider.domain import ResolvedModel, TranscriptionRequest
from src.provider.provider_settings import get_bool as ps_get_bool


_SENSITIVE_SETTING_PARTS = (
    "api_key",
    "access_key",
    "private_key",
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
)


def should_dump_request_json() -> bool:
    """Return whether the active provider/model enables request dumping."""
    return ps_get_bool("dump_request_json", env=None, default=False)


def get_request_dump_dir(provider: str) -> Path:
    safe_provider = re.sub(r"[^A-Za-z0-9_.-]+", "-", provider).strip("-.")
    return (
        Path(tempfile.gettempdir())
        / "CapsWriter-Offline"
        / "request-dumps"
        / (safe_provider or "unknown")
    )


def dump_request_json(
    provider: str,
    request_body: Any,
    request_id: str,
) -> Path | None:
    """Atomically persist an exact, JSON-serializable outbound request payload."""
    if not should_dump_request_json():
        return None

    target_dir = get_request_dump_dir(provider)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_request_id = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(request_id)).strip("-.")
    target = target_dir / f"{timestamp}_{safe_request_id or 'request'}.json"
    temp_path: Path | None = None
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=target_dir,
            prefix=f".{target.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            json.dump(request_body, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, target)
        console.print(
            f"[{provider}:{request_id}] request_dump={target}",
            style="bright_black",
        )
        return target
    except Exception as exc:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception:
                pass
        console.print(
            f"[{provider}:{request_id}] 写入请求 dump 失败：{exc}",
            style="bright_yellow",
        )
        return None


def _safe_value(value: Any) -> Any:
    if isinstance(value, dict):
        return _safe_settings(value)
    if isinstance(value, (list, tuple)):
        return [_safe_value(item) for item in value]
    return value


def _safe_settings(settings: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in dict(settings).items():
        normalized = str(key).lower()
        if any(part in normalized for part in _SENSITIVE_SETTING_PARTS):
            result[str(key)] = "***"
        else:
            result[str(key)] = _safe_value(value)
    return result


def dump_transcription_request(
    request: TranscriptionRequest,
    request_body: Any | None = None,
) -> Path | None:
    """Dump the provider-bound request without changing the audio buffer cursor."""
    if request_body is not None:
        return dump_request_json(
            request.model.ref.provider_id,
            request_body,
            request.task_id,
        )

    position = request.payload_buf.tell()
    try:
        request.payload_buf.seek(0)
        payload = request.payload_buf.read()
    finally:
        request.payload_buf.seek(position)
    body = {
        "provider_id": request.model.ref.provider_id,
        "adapter_type": request.model.adapter_type,
        "model_id": request.model.ref.model_id,
        "upstream_model": request.model.upstream_model,
        "input_mode": request.model.input_mode.value,
        "settings": _safe_settings(request.model.settings),
        "audio": {
            "mime_type": request.payload_mime,
            "data": base64.b64encode(payload).decode("ascii"),
        },
    }
    return dump_request_json(
        request.model.ref.provider_id,
        body,
        request.task_id,
    )


def dump_streaming_session_request(
    model: ResolvedModel,
    task_id: str,
) -> Path | None:
    """Dump the immutable setup request for a live-audio provider session."""
    body = {
        "provider_id": model.ref.provider_id,
        "adapter_type": model.adapter_type,
        "model_id": model.ref.model_id,
        "upstream_model": model.upstream_model,
        "input_mode": model.input_mode.value,
        "settings": _safe_settings(model.settings),
        "audio": {"streaming": True},
    }
    return dump_request_json(model.ref.provider_id, body, task_id)
