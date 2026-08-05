import asyncio
import base64
import io
import json

import httpx

from src.transcribe.bytedance import bytedance_transcribe_http as bytedance
from src.transcribe.providers import ByteDanceProvider, make_provider


def _configure(monkeypatch):
    monkeypatch.setattr(bytedance, "get_api_key", lambda: "key-test")
    monkeypatch.setattr(bytedance, "get_resource_id", lambda: "volc.bigasr.auc_turbo")
    monkeypatch.setattr(
        bytedance, "get_url", lambda: "https://example.test/recognize/flash"
    )
    monkeypatch.setattr(
        bytedance, "ps_get_bool", lambda _key, **kwargs: kwargs["default"]
    )
    monkeypatch.setattr(
        bytedance, "ps_get_str", lambda _key, **kwargs: kwargs.get("default")
    )


def test_realtime_switch_stays_inside_bytedance_provider(monkeypatch):
    from src.transcribe.bytedance import bytedance_transcribe_ws as streaming

    for alias in ("bytedance", "doubao", "volcengine", "volc"):
        provider = make_provider(alias)
        assert isinstance(provider, ByteDanceProvider)
        monkeypatch.setattr(streaming, "should_use_realtime", lambda: False)
        assert provider.supports_streaming_input() is False
        monkeypatch.setattr(streaming, "should_use_realtime", lambda: True)
        assert provider.supports_streaming_input() is True


def test_same_provider_uses_http_after_recording(monkeypatch):
    calls = []

    async def fake_transcribe(*args):
        calls.append(args)
        return "HTTP final", 200, 2.0, 3.0, {"streaming": False}

    monkeypatch.setattr(bytedance, "transcribe_with_retries", fake_transcribe)
    result = asyncio.run(
        ByteDanceProvider().transcribe(
            io.BytesIO(b"wav-audio"),
            "audio/wav",
            "task",
            1.0,
            1.5,
            2,
            0.1,
        )
    )

    assert result[0] == "HTTP final"
    assert result[4] == {"streaming": False}
    assert len(calls) == 1


def test_headers_follow_flash_api_contract(monkeypatch):
    _configure(monkeypatch)

    headers = bytedance.build_headers("request-test")

    assert headers == {
        "Content-Type": "application/json",
        "X-Api-Key": "key-test",
        "X-Api-Resource-Id": "volc.bigasr.auc_turbo",
        "X-Api-Request-Id": "request-test",
        "X-Api-Sequence": "-1",
    }


def test_body_embeds_local_audio_as_base64(monkeypatch):
    _configure(monkeypatch)

    body = bytedance.build_request_body(
        b"wav-audio", "audio/wav", request_id="request-test"
    )

    assert base64.b64decode(body["audio"]["data"]) == b"wav-audio"
    assert body["audio"]["format"] == "wav"
    assert body["audio"]["rate"] == 16000
    assert body["request"] == {
        "model_name": "bigmodel",
        "enable_itn": True,
        "enable_punc": True,
        "enable_ddc": False,
        "show_utterances": True,
    }

    ogg_body = bytedance.build_request_body(
        b"opus-audio", "audio/ogg; codecs=opus", request_id="request-test"
    )
    assert ogg_body["audio"]["format"] == "ogg"
    assert ogg_body["audio"]["codec"] == "opus"


def test_extract_transcript_accepts_direct_and_documentation_wrapper():
    assert bytedance.extract_transcript({"result": {"text": "直接结果"}}) == "直接结果"
    assert (
        bytedance.extract_transcript({"body": {"result": {"text": "包装结果"}}})
        == "包装结果"
    )


def test_transcribe_posts_once_and_reads_api_status_headers(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = request.headers
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            headers={
                "X-Api-Status-Code": "20000000",
                "X-Api-Message": "OK",
                "X-Tt-Logid": "log-test",
            },
            json={
                "audio_info": {"duration": 1800},
                "result": {"text": "非流式结果"},
            },
            request=request,
        )

    async def run_case():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await bytedance._transcribe_once(client, b"wav-audio", "audio/wav")

    text, status, _submit, _complete, meta = asyncio.run(run_case())

    assert text == "非流式结果"
    assert status == 200
    assert captured["headers"]["x-api-sequence"] == "-1"
    assert base64.b64decode(captured["body"]["audio"]["data"]) == b"wav-audio"
    assert meta["api_status_code"] == "20000000"
    assert meta["log_id"] == "log-test"
    assert meta["audio_info"] == {"duration": 1800}


def test_non_success_api_code_returns_diagnostic_metadata(monkeypatch):
    _configure(monkeypatch)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            headers={
                "X-Api-Status-Code": "45000151",
                "X-Api-Message": "audio format is invalid",
                "X-Tt-Logid": "log-error",
            },
            json={},
            request=request,
        )

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    monkeypatch.setattr(bytedance.httpx, "AsyncClient", lambda **_kwargs: mock_client)
    result = asyncio.run(
        bytedance.transcribe_with_retries(
            io.BytesIO(b"bad-audio"),
            "audio/wav",
            "task",
            1.0,
            2.0,
            0,
            0.1,
        )
    )

    text, status, _submit, _complete, meta = result
    assert text == ""
    assert status == 502
    assert meta["api_status_code"] == "45000151"
    assert meta["log_id"] == "log-error"
    assert "audio format is invalid" in meta["error"]
