from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from typing import Any

from src.infra.cosmic import Cosmic
from src.infra.daily_input_stats import get_today_input_count
from src.infra.gui_output import gui_event
from src.gui_api.configuration import (
    get_configuration_snapshot,
    update_asr_prompt,
    update_context_setting,
    update_llm_prompt,
)
from src.gui_api.lexicon import update_lexicon_editor_text
from src.gui_api.tsf_versions import inspect_tsf_dll_versions
from src.personalization.reflection import get_reflection_status_snapshot
from src.personalization.store import (
    clear_personalization_data,
    create_learned_preference,
    delete_learned_preference,
    get_learned_preferences_snapshot,
    update_learned_preference,
)
from src.polish.session_constraint import (
    read_session_constraint,
    write_session_constraint,
)
from src.provider.domain import InputMode, ModelRef
from src.provider.provider_config import provider_manager
from src.tsf_ipc import get_tsf_speech_tip_bridge


GUI_PROTOCOL_VERSION = 1
COMMAND_MARKER = "CW_COMMAND:"
GUI_PROTOCOL_ENV = "CAPSWRITER_GUI_PROTOCOL"


def gui_protocol_enabled() -> bool:
    return os.getenv(GUI_PROTOCOL_ENV, "").strip().casefold() in {"1", "true", "yes"}


def _model_snapshot() -> tuple[dict[str, object] | None, list[dict[str, object]]]:
    active = provider_manager.get_active_model()
    active_snapshot: dict[str, object] | None = None
    if active is not None:
        active_snapshot = {
            "provider_id": active.ref.provider_id,
            "model_id": active.ref.model_id,
            "provider_name": active.provider_name,
            "model_name": active.model_name,
            "label": f"{active.model_name} · {active.provider_name}",
            "input_mode": active.input_mode.value,
            "input_modes": sorted(mode.value for mode in active.input_modes),
            "incremental_output": active.incremental_output,
        }
    models: list[dict[str, object]] = []
    for info in provider_manager.list_models():
        ref = info["ref"]
        if not isinstance(ref, ModelRef):
            continue
        models.append(
            {
                "provider_id": ref.provider_id,
                "model_id": ref.model_id,
                "provider_name": str(info["provider_name"]),
                "model_name": str(info["name"]),
                "label": str(info["label"]),
                "input_modes": sorted(mode.value for mode in info["input_modes"]),
                "input_mode": provider_manager.get_model_mode(ref).value,
                "incremental_output": bool(info["incremental_output"]),
                "active": bool(info["active"]),
            }
        )
    return active_snapshot, models


def build_service_snapshot() -> dict[str, object]:
    active_model, models = _model_snapshot()
    bridge = get_tsf_speech_tip_bridge()
    return {
        "protocol_version": GUI_PROTOCOL_VERSION,
        "service": {
            "process_id": os.getpid(),
            "recording": bool(Cosmic.on),
            "transcribing": bool(getattr(Cosmic, "transcribe_busy", False)),
            "active_task_id": getattr(Cosmic, "active_task_id", None),
        },
        "active_model": active_model,
        "models": models,
        "daily_input_count": get_today_input_count(),
        "session_constraint": read_session_constraint(),
        "reflection": get_reflection_status_snapshot(),
        "tsf": bridge.get_status_snapshot(),
    }


async def _dispatch_command(command: str, payload: dict[str, Any]) -> object:
    if command == "get_snapshot":
        return await asyncio.to_thread(build_service_snapshot)
    if command == "set_session_constraint":
        text = str(payload.get("text", ""))
        await asyncio.to_thread(write_session_constraint, text)
        return {"session_constraint": await asyncio.to_thread(read_session_constraint)}
    if command == "get_configuration":
        return await asyncio.to_thread(get_configuration_snapshot)
    if command == "get_polish_history":
        from src.polish.llm_polish import get_finalized_history

        return {"items": await asyncio.to_thread(get_finalized_history)}
    if command == "get_learned_preferences":
        return await asyncio.to_thread(
            get_learned_preferences_snapshot,
            limit=int(payload.get("limit", 500)),
        )
    if command == "create_learned_preference":
        avoid_values = payload.get("avoid_values", [])
        keywords = payload.get("keywords", [])
        if not isinstance(avoid_values, list) or not isinstance(keywords, list):
            raise ValueError("avoid_values and keywords must be arrays")
        preference_id = await asyncio.to_thread(
            create_learned_preference,
            kind=str(payload.get("kind", "")),
            preferred_value=str(payload.get("preferred_value", "")),
            avoid_values=avoid_values,
            keywords=keywords,
            status=str(payload.get("status", "")),
        )
        return {"created": preference_id}
    if command == "update_learned_preference":
        avoid_values = payload.get("avoid_values", [])
        keywords = payload.get("keywords", [])
        if not isinstance(avoid_values, list) or not isinstance(keywords, list):
            raise ValueError("avoid_values and keywords must be arrays")
        preference_id = int(payload.get("preference_id", 0))
        await asyncio.to_thread(
            update_learned_preference,
            preference_id=preference_id,
            kind=str(payload.get("kind", "")),
            preferred_value=str(payload.get("preferred_value", "")),
            avoid_values=avoid_values,
            keywords=keywords,
            status=str(payload.get("status", "")),
        )
        return {"updated": preference_id}
    if command == "delete_learned_preference":
        preference_id = int(payload.get("preference_id", 0))
        deleted = await asyncio.to_thread(delete_learned_preference, preference_id)
        if not deleted:
            raise ValueError("learned preference does not exist")
        return {"deleted": preference_id}
    if command == "clear_personalization":
        return await asyncio.to_thread(clear_personalization_data)
    if command == "inspect_tsf_dll_versions":
        return await asyncio.to_thread(inspect_tsf_dll_versions)
    if command == "set_asr_prompt":
        return await asyncio.to_thread(
            update_asr_prompt,
            str(payload.get("provider_id", "")),
            str(payload.get("text", "")),
        )
    if command == "set_llm_prompt":
        return await asyncio.to_thread(
            update_llm_prompt,
            str(payload.get("text", "")),
        )
    if command == "set_context_setting":
        name = str(payload.get("name", ""))
        enabled = bool(payload.get("enabled", False))
        result = await asyncio.to_thread(
            update_context_setting,
            name,
            enabled,
        )
        if name == "vision":
            from src.polish.vision_context import (
                start_vision_context_service,
                stop_vision_context_service,
            )

            vision_task = getattr(Cosmic, "vision_context_task", None)
            if enabled and (vision_task is None or vision_task.done()):
                start_vision_context_service()
            elif not enabled:
                await stop_vision_context_service(vision_task)
        return result
    if command == "set_lexicon":
        return await asyncio.to_thread(
            update_lexicon_editor_text,
            str(payload.get("text", "")),
        )
    if command == "set_active_model":
        ref = ModelRef(
            str(payload.get("provider_id", "")).strip(),
            str(payload.get("model_id", "")).strip(),
        )
        if not ref.provider_id or not ref.model_id:
            raise ValueError("provider_id and model_id are required")
        if not provider_manager.set_active_model(ref):
            raise ValueError(f"unknown transcription model: {ref.key}")
        return {"active_model": ref.key, "apply_mode": "next_recording"}
    if command == "set_model_mode":
        ref = ModelRef(
            str(payload.get("provider_id", "")).strip(),
            str(payload.get("model_id", "")).strip(),
        )
        mode = InputMode(str(payload.get("mode", "")))
        if not provider_manager.set_model_mode(ref, mode):
            raise ValueError(f"cannot set {ref.key} to {mode.value}")
        return {"model": ref.key, "mode": mode.value, "apply_mode": "next_recording"}
    if command == "reload_providers":
        if Cosmic.on or bool(getattr(Cosmic, "transcribe_busy", False)):
            raise RuntimeError("当前正在录音或识别，不能重新加载转录服务")
        await asyncio.to_thread(provider_manager.load_providers)
        active, models = await asyncio.to_thread(_model_snapshot)
        return {"active_model": active, "models": models}
    if command == "retry_latest":
        if Cosmic.on or bool(getattr(Cosmic, "transcribe_busy", False)):
            raise RuntimeError("当前正在录音或识别，不能重试最近录音")

        from src.audio.send_audio import retry_latest_audio

        asyncio.create_task(retry_latest_audio(), name="native_gui_retry_latest")
        return {"accepted": True}
    if command == "get_latest_wav":
        from src.audio.retry_cache import latest_audio_path_for_mime

        path = latest_audio_path_for_mime("audio/wav")
        available = path.exists() and path.is_file() and path.stat().st_size > 0
        return {"path": str(path) if available else None}
    if command == "abandon_current":
        from src.keyboard.shortcut_handler import abandon_current_task

        abandon_current_task()
        return {"accepted": True}
    if command == "clear_history":
        from src.polish.llm_polish import clear_finalized_history

        cleared = await asyncio.to_thread(clear_finalized_history)
        return {"cleared": cleared}
    raise ValueError(f"unknown GUI command: {command}")


async def run_gui_command_loop() -> None:
    if not gui_protocol_enabled():
        return
    gui_event("protocol_hello", protocol_version=GUI_PROTOCOL_VERSION)
    loop = asyncio.get_running_loop()
    lines: asyncio.Queue[str | None] = asyncio.Queue()

    def read_stdin() -> None:
        try:
            for line in sys.stdin:
                loop.call_soon_threadsafe(lines.put_nowait, line)
        finally:
            loop.call_soon_threadsafe(lines.put_nowait, None)

    threading.Thread(
        target=read_stdin,
        name="native_gui_stdin_reader",
        daemon=True,
    ).start()
    while True:
        line = await lines.get()
        if line is None:
            return
        line = line.strip()
        if not line.startswith(COMMAND_MARKER):
            continue
        request_id: object = None
        try:
            payload = json.loads(line[len(COMMAND_MARKER) :])
            if not isinstance(payload, dict):
                raise ValueError("command payload must be an object")
            request_id = payload.get("request_id")
            if payload.get("protocol_version") != GUI_PROTOCOL_VERSION:
                raise ValueError(
                    f"unsupported GUI protocol version: {payload.get('protocol_version')}"
                )
            command = str(payload.get("command", ""))
            result = await _dispatch_command(command, payload)
            gui_event(
                "command_result",
                protocol_version=GUI_PROTOCOL_VERSION,
                request_id=request_id,
                command=command,
                ok=True,
                result=result,
            )
        except Exception as exc:  # noqa: BLE001 - report command failures to the GUI
            gui_event(
                "command_result",
                protocol_version=GUI_PROTOCOL_VERSION,
                request_id=request_id,
                ok=False,
                error={"type": type(exc).__name__, "message": str(exc)},
            )


async def publish_gui_snapshots(interval_seconds: float = 1.0) -> None:
    if not gui_protocol_enabled():
        return
    previous = ""
    while True:
        try:
            snapshot = await asyncio.to_thread(build_service_snapshot)
            serialized = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            if serialized != previous:
                gui_event("service_snapshot", snapshot=snapshot)
                previous = serialized
        except Exception as exc:  # noqa: BLE001 - diagnostics must not stop the client
            gui_event(
                "service_snapshot_error",
                error_type=type(exc).__name__,
                message=str(exc),
            )
        await asyncio.sleep(max(0.25, float(interval_seconds)))
