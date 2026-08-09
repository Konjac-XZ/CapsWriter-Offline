import asyncio
import io
from types import SimpleNamespace
from typing import Any, cast

from src.gui_api import service
from src.provider.domain import InputMode, ModelRef


class _ProviderManager:
    def __init__(self):
        self.selected = ModelRef("provider-a", "model-a")

    def get_active_model(self):
        return SimpleNamespace(
            ref=self.selected,
            provider_name="Provider A",
            model_name="Model A",
            input_mode=InputMode.LIVE_AUDIO,
            input_modes=frozenset({InputMode.FILE_UPLOAD, InputMode.LIVE_AUDIO}),
            incremental_output=True,
        )

    def list_models(self):
        return [
            {
                "ref": ModelRef("provider-a", "model-a"),
                "provider_name": "Provider A",
                "name": "Model A",
                "label": "Model A · Provider A",
                "input_modes": frozenset({InputMode.FILE_UPLOAD, InputMode.LIVE_AUDIO}),
                "incremental_output": True,
                "active": True,
            }
        ]

    def get_model_mode(self, _ref):
        return InputMode.LIVE_AUDIO

    def set_active_model(self, ref):
        self.selected = ref
        return True

    def set_model_mode(self, _ref, _mode):
        return True


def test_build_service_snapshot_is_versioned_and_content_free(monkeypatch):
    monkeypatch.setattr(service, "provider_manager", _ProviderManager())
    monkeypatch.setattr(service, "get_today_input_count", lambda: 1234)
    monkeypatch.setattr(service, "read_session_constraint", lambda: "保持简短")
    monkeypatch.setattr(
        service,
        "get_reflection_status_snapshot",
        lambda: {"enabled": True, "phase": "idle"},
    )
    monkeypatch.setattr(
        service,
        "get_tsf_speech_tip_bridge",
        lambda: SimpleNamespace(
            get_status_snapshot=lambda: {
                "enabled": True,
                "client_count": 1,
                "composition": {"active": False},
            }
        ),
    )

    snapshot = service.build_service_snapshot()

    active_model = cast(dict[str, Any], snapshot["active_model"])
    models = cast(list[dict[str, Any]], snapshot["models"])
    assert snapshot["protocol_version"] == 1
    assert active_model["provider_id"] == "provider-a"
    assert models[0]["input_mode"] == "live_audio"
    assert snapshot["daily_input_count"] == 1234
    assert snapshot["session_constraint"] == "保持简短"
    assert "api_key" not in repr(snapshot).casefold()


def test_set_session_constraint_returns_persisted_value(monkeypatch):
    stored = {"value": ""}
    monkeypatch.setattr(
        service,
        "write_session_constraint",
        lambda text: stored.__setitem__("value", text.strip()),
    )
    monkeypatch.setattr(service, "read_session_constraint", lambda: stored["value"])

    result = asyncio.run(
        service._dispatch_command("set_session_constraint", {"text": "  只输出一句  "})
    )

    assert result == {"session_constraint": "只输出一句"}


def test_get_learned_preferences_is_loaded_on_demand(monkeypatch):
    expected = {
        "total": 1,
        "items": [{"preferred_value": "TypeScript", "status": "active"}],
    }
    monkeypatch.setattr(
        service,
        "get_learned_preferences_snapshot",
        lambda *, limit: expected | {"limit": limit},
    )

    result = asyncio.run(
        service._dispatch_command("get_learned_preferences", {"limit": 200})
    )

    assert result == expected | {"limit": 200}


def test_tsf_dll_inspection_is_loaded_on_demand(monkeypatch):
    expected = {
        "latest": {"version": "20260809-build", "dlls": {}},
        "hosts": [{"process_name": "explorer.exe", "status": "latest"}],
        "warnings": [],
        "latest_count": 1,
        "old_count": 0,
        "unknown_count": 0,
    }
    monkeypatch.setattr(service, "inspect_tsf_dll_versions", lambda: expected)

    result = asyncio.run(service._dispatch_command("inspect_tsf_dll_versions", {}))

    assert result == expected


def test_command_loop_emits_correlated_result(monkeypatch):
    events = []
    monkeypatch.setenv(service.GUI_PROTOCOL_ENV, "1")
    monkeypatch.setattr(
        service.sys,
        "stdin",
        io.StringIO(
            'CW_COMMAND:{"protocol_version":1,"request_id":"abc","command":"get_snapshot"}\n'
        ),
    )
    monkeypatch.setattr(
        service, "gui_event", lambda event, **payload: events.append((event, payload))
    )
    monkeypatch.setattr(
        service, "build_service_snapshot", lambda: {"protocol_version": 1}
    )

    asyncio.run(service.run_gui_command_loop())

    assert events[0] == ("protocol_hello", {"protocol_version": 1})
    assert events[1][0] == "command_result"
    assert events[1][1]["request_id"] == "abc"
    assert events[1][1]["ok"] is True


def test_command_loop_rejects_unknown_protocol_version(monkeypatch):
    events = []
    monkeypatch.setenv(service.GUI_PROTOCOL_ENV, "1")
    monkeypatch.setattr(
        service.sys,
        "stdin",
        io.StringIO(
            'CW_COMMAND:{"protocol_version":2,"request_id":"old","command":"get_snapshot"}\n'
        ),
    )
    monkeypatch.setattr(
        service, "gui_event", lambda event, **payload: events.append((event, payload))
    )

    asyncio.run(service.run_gui_command_loop())

    assert events[1][0] == "command_result"
    assert events[1][1]["request_id"] == "old"
    assert events[1][1]["ok"] is False
    assert events[1][1]["error"]["type"] == "ValueError"


def test_reload_providers_rejects_active_recording(monkeypatch):
    monkeypatch.setattr(service.Cosmic, "on", True)

    try:
        asyncio.run(service._dispatch_command("reload_providers", {}))
    except RuntimeError as exception:
        assert "正在录音或识别" in str(exception)
    else:
        raise AssertionError("reload_providers should reject an active recording")
