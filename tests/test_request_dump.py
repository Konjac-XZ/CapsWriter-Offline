import asyncio
import base64
import io
import json
from types import MappingProxyType

from src.provider.domain import (
    InputMode,
    ModelRef,
    ResolvedModel,
    TranscriptionRequest,
)
from src.provider.provider_settings import use_resolved_model
from src.transcribe import request_dump
from src.transcribe.providers import TranscriptionProvider


def _resolved(*, enabled: bool, mode: InputMode = InputMode.FILE_UPLOAD):
    return ResolvedModel(
        ref=ModelRef("custom-provider", "shared"),
        provider_name="Custom Provider",
        adapter_type="openai",
        model_name="Shared ASR",
        upstream_model="upstream-model",
        input_mode=mode,
        input_modes=frozenset({InputMode.FILE_UPLOAD, InputMode.LIVE_AUDIO}),
        incremental_output=False,
        settings=MappingProxyType(
            {
                "model": "upstream-model",
                "dump_request_json": enabled,
                "prompt": "Private meeting context",
                "api_key": "must-not-leak",
                "secret_key": "also-must-not-leak",
            }
        ),
    )


class _Adapter(TranscriptionProvider):
    def name(self) -> str:
        return "openai"

    async def transcribe(self, *args, **kwargs):
        return "text", 200, 1.0, 2.0, {}


def _request(enabled: bool) -> TranscriptionRequest:
    return TranscriptionRequest(
        payload_buf=io.BytesIO(b"audio-bytes"),
        payload_mime="audio/wav",
        task_id="task/unsafe",
        time_start=0.0,
        record_stop=1.0,
        max_retries=1,
        base_delay=0.0,
        model=_resolved(enabled=enabled),
    )


def test_request_dump_is_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(
        request_dump, "get_request_dump_dir", lambda provider: tmp_path / provider
    )
    monkeypatch.setattr(
        _Adapter,
        "build_request_dump_body",
        lambda self, request: (_ for _ in ()).throw(
            AssertionError("disabled dump must not build a payload")
        ),
    )

    asyncio.run(_Adapter().transcribe_request(_request(False)))

    assert list(tmp_path.rglob("*.json")) == []


def test_provider_setting_dumps_request_and_redacts_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(
        request_dump, "get_request_dump_dir", lambda provider: tmp_path / provider
    )
    request = _request(True)
    request.payload_buf.seek(3)

    asyncio.run(_Adapter().transcribe_request(request))

    paths = list((tmp_path / "custom-provider").glob("*.json"))
    assert len(paths) == 1
    assert paths[0].name.endswith("_task-unsafe.json")
    body = json.loads(paths[0].read_text(encoding="utf-8"))
    assert body["provider_id"] == "custom-provider"
    assert body["adapter_type"] == "openai"
    assert body["upstream_model"] == "upstream-model"
    assert body["settings"]["prompt"] == "Private meeting context"
    assert body["settings"]["api_key"] == "***"
    assert body["settings"]["secret_key"] == "***"
    assert base64.b64decode(body["audio"]["data"]) == b"audio-bytes"
    assert request.payload_buf.tell() == 3
    assert list((tmp_path / "custom-provider").glob("*.tmp")) == []


def test_live_audio_session_uses_the_same_provider_setting(tmp_path, monkeypatch):
    monkeypatch.setattr(
        request_dump, "get_request_dump_dir", lambda provider: tmp_path / provider
    )
    model = _resolved(enabled=True, mode=InputMode.LIVE_AUDIO)

    with use_resolved_model(model):
        request_dump.dump_streaming_session_request(model, "live-task")

    [path] = list((tmp_path / "custom-provider").glob("*.json"))
    body = json.loads(path.read_text(encoding="utf-8"))
    assert body["input_mode"] == "live_audio"
    assert body["audio"] == {"streaming": True}
