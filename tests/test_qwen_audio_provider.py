import asyncio
import base64
import io
import json

import httpx

from src.provider.provider_config import ProviderManager
from src.transcribe import api as transcribe_api
from src.transcribe.providers import QwenAudioProvider, make_provider
from src.transcribe.qwen_audio import qwen_audio_transcribe_http as qwen_audio


def test_make_provider_supports_qwen_audio_aliases():
    for provider_type in (
        "qwen-audio",
        "qwen_audio_3",
        "qwen-audio-3",
        "qwen_audio",
        "alibaba_qwen_audio_3",
    ):
        provider = make_provider(provider_type)
        assert isinstance(provider, QwenAudioProvider)


def test_workspace_endpoint_uses_beijing_dedicated_domain(monkeypatch):
    values = {
        "endpoint": None,
        "workspace_id": "ws-test",
        "region": "cn-beijing",
    }
    monkeypatch.setattr(
        qwen_audio,
        "ps_get_str",
        lambda key, **kwargs: values.get(key, kwargs.get("default")),
    )

    assert qwen_audio.get_api_url() == (
        "https://ws-test.cn-beijing.maas.aliyuncs.com"
        "/api/v1/services/aigc/multimodal-generation/generation"
    )


def test_public_base_url_uses_generation_endpoint(monkeypatch):
    values = {
        "endpoint": None,
        "workspace_id": None,
        "base_url": "https://dashscope.aliyuncs.com",
    }
    monkeypatch.setattr(
        qwen_audio,
        "ps_get_str",
        lambda key, **kwargs: values.get(key, kwargs.get("default")),
    )

    assert qwen_audio.get_api_url().endswith(
        "/api/v1/services/aigc/multimodal-generation/generation"
    )


def test_headers_explicitly_disable_sse():
    headers = qwen_audio.build_headers("sk-test")

    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["X-DashScope-SSE"] == "disable"
    assert headers["Content-Type"] == "application/json"


def test_build_mp3_request_uses_data_uri_and_actual_format(monkeypatch):
    monkeypatch.setattr(qwen_audio, "get_model", lambda: "qwen-audio-3.0-asr-flash")
    monkeypatch.setattr(qwen_audio, "get_language_hints", lambda: ["zh", "en"])
    monkeypatch.setattr(qwen_audio, "get_vocabulary", lambda: {"CapsWriter": 5})
    monkeypatch.setattr(qwen_audio, "ps_get_int", lambda *args, **kwargs: None)

    audio_b64 = base64.b64encode(b"mp3-audio").decode("ascii")
    body = qwen_audio.build_request_body("audio/mpeg", audio_b64)

    assert body == {
        "model": "qwen-audio-3.0-asr-flash",
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": f"data:audio/mpeg;base64,{audio_b64}",
                            },
                        }
                    ],
                }
            ]
        },
        "parameters": {
            "format": "mp3",
            "language_hints": ["zh", "en"],
            "vocabulary": {"CapsWriter": 5},
        },
    }


def test_build_wav_request_does_not_invent_sample_rate(monkeypatch):
    monkeypatch.setattr(qwen_audio, "get_model", lambda: "qwen-audio-3.0-asr-flash")
    monkeypatch.setattr(qwen_audio, "get_language_hints", lambda: [])
    monkeypatch.setattr(qwen_audio, "get_vocabulary", lambda: {})
    monkeypatch.setattr(qwen_audio, "ps_get_int", lambda *args, **kwargs: None)
    monkeypatch.setattr(qwen_audio, "ps_get_str", lambda *args, **kwargs: None)

    body = qwen_audio.build_request_body("audio/x-wav", "YWJj")

    assert body["parameters"] == {"format": "wav"}
    assert body["input"]["messages"][0]["content"][0]["input_audio"]["data"] == (
        "data:audio/wav;base64,YWJj"
    )


def test_payload_logging_requires_debug_and_explicit_switch(monkeypatch):
    values = {"debug": False, "log_request_payload": True}
    monkeypatch.setattr(
        qwen_audio,
        "ps_get_bool",
        lambda name, **kwargs: values.get(name, kwargs.get("default", False)),
    )

    assert qwen_audio.should_log_request_payload() is False

    values["debug"] = True
    assert qwen_audio.should_log_request_payload() is True


def test_debug_payload_removes_audio_without_changing_request():
    request_body = {
        "model": "qwen-audio-3.0-asr-flash",
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "上下文"}],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": "data:audio/wav;base64,SECRET_AUDIO"
                            },
                        }
                    ],
                },
            ]
        },
        "parameters": {"format": "wav", "vocabulary": {"QEMU": 4}},
    }

    debug_body = qwen_audio.request_body_for_debug(request_body)

    assert debug_body["input"]["messages"][0]["content"][0]["text"] == "上下文"
    assert debug_body["parameters"]["vocabulary"] == {"QEMU": 4}
    assert debug_body["input"]["messages"][1]["content"] == [
        {"type": "input_audio"}
    ]
    assert "SECRET_AUDIO" not in json.dumps(debug_body)
    assert request_body["input"]["messages"][1]["content"][0]["input_audio"][
        "data"
    ].endswith("SECRET_AUDIO")


def test_log_request_payload_outputs_sanitized_json(monkeypatch):
    messages = []
    request_body = {
        "model": "qwen-audio-3.0-asr-flash",
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {"data": "SECRET_AUDIO"},
                        }
                    ],
                }
            ]
        },
        "parameters": {"format": "wav"},
    }
    monkeypatch.setattr(qwen_audio, "should_log_request_payload", lambda: True)
    monkeypatch.setattr(
        qwen_audio.console,
        "print",
        lambda message, **kwargs: messages.append(str(message)),
    )

    qwen_audio._log_request_payload(request_body)

    assert len(messages) == 1
    assert "request-payload; audio omitted" in messages[0]
    assert '"type": "input_audio"' in messages[0]
    assert '"input_audio":' not in messages[0]
    assert "SECRET_AUDIO" not in messages[0]


def test_extract_transcript_supports_documented_shapes():
    assert qwen_audio.extract_transcript(
        {"output": {"sentence": {"text": "sentence"}, "text": "full text"}}
    ) == "full text"
    assert qwen_audio.extract_transcript(
        {"output": {"output": {"sentence": {"text": "nested sentence"}}}}
    ) == "nested sentence"


def test_language_hints_accept_yaml_list_and_cap_at_four():
    assert qwen_audio._parse_language_hints(["zh", "en", "JA", "ko", "fr"]) == [
        "zh",
        "en",
        "ja",
        "ko",
    ]


def test_vocabulary_rejects_invalid_weights():
    assert qwen_audio._parse_vocabulary(
        {"CapsWriter": 5, "super": 50, "too-high": 6, "bad": "no"}
    ) == {"CapsWriter": 5, "super": 50}


def test_normalize_hotword_text_unwraps_only_whole_markdown_wrappers():
    assert qwen_audio.normalize_hotword_text("\\`pyan3`\\") == "pyan3"
    assert qwen_audio.normalize_hotword_text("\\`docs\\`") == "docs"
    assert qwen_audio.normalize_hotword_text("`docs`") == "docs"
    assert qwen_audio.normalize_hotword_text("AGENTS.md") == "AGENTS.md"


def test_hotword_text_limits_match_qwen_audio_rules():
    assert qwen_audio.validate_hotword_text("EGFR抑制剂") is None
    assert qwen_audio.validate_hotword_text("一" * 15) is None
    assert "上限为 15" in qwen_audio.validate_hotword_text("一" * 16)
    assert qwen_audio.validate_hotword_text("one two three four five six seven") is None
    assert "上限为 7" in qwen_audio.validate_hotword_text(
        "one two three four five six seven eight"
    )


def test_resolve_vocabulary_merges_live_lexicon_and_explicit_overrides(monkeypatch):
    monkeypatch.setattr(qwen_audio, "should_use_user_lexicon", lambda: True)
    monkeypatch.setattr(qwen_audio, "get_user_lexicon_weight", lambda: 4)
    monkeypatch.setattr(
        qwen_audio,
        "load_user_lexicon_words",
        lambda: ["Ubuntu", "Ubuntu", "\\`pyan3`\\", "一" * 16],
    )
    monkeypatch.setattr(
        qwen_audio,
        "_raw_provider_vocabulary",
        lambda: {"Ubuntu": 5, "CapsWriter": 50},
    )

    vocabulary, issues = qwen_audio.resolve_vocabulary()

    assert vocabulary == {"Ubuntu": 5, "pyan3": 4, "CapsWriter": 50}
    assert len(issues) == 2
    assert any("重复，已去重" in issue for issue in issues)
    assert any("上限为 15" in issue for issue in issues)


def test_vocabulary_warning_is_short_and_does_not_echo_user_terms(monkeypatch):
    messages = []
    monkeypatch.setattr(
        qwen_audio.console,
        "print",
        lambda message, **kwargs: messages.append(str(message)),
    )
    qwen_audio._LAST_VOCABULARY_WARNING = None

    qwen_audio._report_vocabulary_issues(
        [
            "用户词库 'Ubuntu' 重复，已去重",
            "用户词库 'private-term'：包含非 ASCII 字符且长度为 33，上限为 15",
        ],
        69,
    )

    assert messages == [
        "Qwen Audio 3.0 热词已处理：发送 69 条；去重 1 条，长度超限 1 条。"
    ]
    assert "Ubuntu" not in messages[0]
    assert "private-term" not in messages[0]


def test_resolve_vocabulary_can_disable_gui_lexicon(monkeypatch):
    monkeypatch.setattr(qwen_audio, "should_use_user_lexicon", lambda: False)
    monkeypatch.setattr(qwen_audio, "load_user_lexicon_words", lambda: ["not-sent"])
    monkeypatch.setattr(
        qwen_audio,
        "_raw_provider_vocabulary",
        lambda: {"explicit": 4},
    )

    vocabulary, issues = qwen_audio.resolve_vocabulary()

    assert vocabulary == {"explicit": 4}
    assert issues == []


def test_resolve_vocabulary_enforces_super_hotword_limit(monkeypatch):
    monkeypatch.setattr(qwen_audio, "should_use_user_lexicon", lambda: False)
    monkeypatch.setattr(
        qwen_audio,
        "_raw_provider_vocabulary",
        lambda: {f"term{i}": 50 for i in range(51)},
    )

    vocabulary, issues = qwen_audio.resolve_vocabulary()

    assert len(vocabulary) == 50
    assert all(weight == 50 for weight in vocabulary.values())
    assert any("超过 50 条上限" in issue for issue in issues)


def test_resolve_vocabulary_caps_total_and_prioritizes_explicit_terms(monkeypatch):
    monkeypatch.setattr(qwen_audio, "should_use_user_lexicon", lambda: True)
    monkeypatch.setattr(qwen_audio, "get_user_lexicon_weight", lambda: 4)
    monkeypatch.setattr(
        qwen_audio,
        "load_user_lexicon_words",
        lambda: [f"word{i}" for i in range(2001)],
    )
    monkeypatch.setattr(
        qwen_audio,
        "_raw_provider_vocabulary",
        lambda: {"priority": 5},
    )

    vocabulary, issues = qwen_audio.resolve_vocabulary()

    assert len(vocabulary) == 2000
    assert vocabulary["priority"] == 5
    assert any("超过 2000 条上限" in issue for issue in issues)


def test_provider_yaml_is_discoverable_with_hotword_defaults():
    manager = ProviderManager()

    provider = manager.get_provider("qwen_audio_3")
    assert provider is not None
    assert provider.type == "qwen-audio"
    assert provider.settings["model"] == "qwen-audio-3.0-asr-flash"
    assert provider.settings["realtime"] is True
    assert (
        provider.settings["realtime_model"]
        == "qwen-audio-3.0-asr-flash-streaming"
    )
    assert provider.settings["realtime_format"] == "pcm"
    assert provider.settings["realtime_sample_rate"] == 16000
    assert provider.settings["realtime_chunk_ms"] == 100
    assert provider.settings["use_user_lexicon"] is True
    assert provider.settings["user_lexicon_weight"] == 4
    assert isinstance(provider.settings["debug"], bool)
    assert isinstance(provider.settings["log_request_payload"], bool)


def test_transcribe_api_dispatches_qwen_audio_3_provider(monkeypatch):
    class FakeManager:
        @staticmethod
        def get_active_provider_type():
            return "qwen-audio"

    class FakeProvider:
        async def transcribe(self, *args, **kwargs):
            assert kwargs["request_context"] is None
            return "已接入", 200, 1.0, 2.0, {"provider": "qwen-audio"}

    selected = []

    def fake_make_provider(kind):
        selected.append(kind)
        return FakeProvider()

    monkeypatch.setattr(transcribe_api, "provider_manager", FakeManager())
    monkeypatch.setattr(transcribe_api, "make_provider", fake_make_provider)

    result = asyncio.run(
        transcribe_api.transcribe_audio(
            io.BytesIO(b"RIFF-test"),
            "audio/wav",
            "task",
            1.0,
            2.0,
            1,
            0.0,
        )
    )

    assert selected == ["qwen-audio"]
    assert result[0] == "已接入"
    assert result[4]["provider"] == "qwen-audio"


def test_http_transport_posts_documented_request_and_parses_response(monkeypatch):
    captured = {}
    monkeypatch.setattr(qwen_audio, "should_log_request_payload", lambda: False)

    class FakeClient:
        async def post(self, url, *, headers, json):
            captured.update(url=url, headers=headers, body=json)
            request = httpx.Request("POST", url)
            return httpx.Response(
                200,
                request=request,
                json={
                    "output": {
                        "sentence": {"text": "你好，CapsWriter。"},
                        "text": "你好，CapsWriter。",
                    },
                    "usage": {"duration": 2},
                    "request_id": "req-test",
                },
            )

    monkeypatch.setattr(qwen_audio, "get_api_key", lambda: "sk-test")
    monkeypatch.setattr(qwen_audio, "get_api_url", lambda: "https://example.test/generation")
    monkeypatch.setattr(qwen_audio, "get_http_client", lambda: FakeClient())
    monkeypatch.setattr(qwen_audio, "get_model", lambda: "qwen-audio-3.0-asr-flash")
    monkeypatch.setattr(qwen_audio, "get_language_hints", lambda: ["zh", "en"])
    monkeypatch.setattr(qwen_audio, "get_vocabulary", lambda: {})
    monkeypatch.setattr(qwen_audio, "ps_get_int", lambda *args, **kwargs: None)
    monkeypatch.setattr(qwen_audio, "ps_get_str", lambda *args, **kwargs: None)

    result = asyncio.run(
        qwen_audio.transcribe_with_retries(
            io.BytesIO(b"RIFF-test"),
            "audio/wav",
            "task",
            1.0,
            2.0,
            1,
            0.0,
        )
    )

    text, status, _t_submit, _t_complete, meta = result
    assert text == "你好，CapsWriter。"
    assert status == 200
    assert meta["request_id"] == "req-test"
    assert meta["usage"] == {"duration": 2}
    assert meta["vocabulary_count"] == 0
    assert captured["headers"]["X-DashScope-SSE"] == "disable"
    assert captured["body"]["parameters"]["format"] == "wav"
    assert captured["body"]["input"]["messages"][0]["content"][0]["input_audio"][
        "data"
    ].startswith("data:audio/wav;base64,")


def test_http_transport_retries_transient_status(monkeypatch):
    monkeypatch.setattr(qwen_audio, "should_log_request_payload", lambda: False)
    calls = 0
    sleeps = []

    class FakeClient:
        async def post(self, url, *, headers, json):
            nonlocal calls
            calls += 1
            request = httpx.Request("POST", url)
            if calls == 1:
                return httpx.Response(503, request=request, json={"message": "busy"})
            return httpx.Response(
                200,
                request=request,
                json={"output": {"text": "重试成功"}},
            )

    async def fake_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr(qwen_audio, "get_api_key", lambda: "sk-test")
    monkeypatch.setattr(qwen_audio, "get_api_url", lambda: "https://example.test/generation")
    monkeypatch.setattr(qwen_audio, "get_http_client", lambda: FakeClient())
    monkeypatch.setattr(qwen_audio, "get_model", lambda: "qwen-audio-3.0-asr-flash")
    monkeypatch.setattr(qwen_audio, "get_language_hints", lambda: [])
    monkeypatch.setattr(qwen_audio, "get_vocabulary", lambda: {})
    monkeypatch.setattr(qwen_audio, "ps_get_int", lambda *args, **kwargs: None)
    monkeypatch.setattr(qwen_audio, "ps_get_str", lambda *args, **kwargs: None)
    monkeypatch.setattr(qwen_audio.asyncio, "sleep", fake_sleep)

    text, status, *_ = asyncio.run(
        qwen_audio.transcribe_with_retries(
            io.BytesIO(b"ID3-test"),
            "audio/mpeg",
            "task",
            1.0,
            2.0,
            2,
            0.25,
        )
    )

    assert text == "重试成功"
    assert status == 200
    assert calls == 2
    assert sleeps == [0.25]


def test_http_error_is_reported_in_metadata_not_transcribed(monkeypatch):
    monkeypatch.setattr(qwen_audio, "should_log_request_payload", lambda: False)
    class FakeClient:
        async def post(self, url, *, headers, json):
            request = httpx.Request("POST", url)
            return httpx.Response(
                400,
                request=request,
                json={"message": "invalid payload"},
            )

    monkeypatch.setattr(qwen_audio, "get_api_key", lambda: "sk-test")
    monkeypatch.setattr(qwen_audio, "get_api_url", lambda: "https://example.test/generation")
    monkeypatch.setattr(qwen_audio, "get_http_client", lambda: FakeClient())
    monkeypatch.setattr(qwen_audio, "get_model", lambda: "qwen-audio-3.0-asr-flash")
    monkeypatch.setattr(qwen_audio, "get_language_hints", lambda: [])
    monkeypatch.setattr(qwen_audio, "get_vocabulary", lambda: {})
    monkeypatch.setattr(qwen_audio, "ps_get_int", lambda *args, **kwargs: None)
    monkeypatch.setattr(qwen_audio, "ps_get_str", lambda *args, **kwargs: None)
    monkeypatch.setattr(qwen_audio, "_report_error", lambda message: None)

    text, status, _t_submit, _t_complete, meta = asyncio.run(
        qwen_audio.transcribe_with_retries(
            io.BytesIO(b"RIFF-test"),
            "audio/wav",
            "task",
            1.0,
            2.0,
            3,
            0.0,
        )
    )

    assert text == ""
    assert status == 400
    assert meta["error"] == "invalid payload"
