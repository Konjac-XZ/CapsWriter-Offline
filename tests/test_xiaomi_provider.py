import base64

from src.transcribe.providers import XiaomiProvider, make_provider
from src.transcribe.xiaomi import xiaomi_transcribe_http as xiaomi


def test_make_provider_supports_xiaomi():
    assert isinstance(make_provider("xiaomi"), XiaomiProvider)
    assert isinstance(make_provider("mimo"), XiaomiProvider)
    assert isinstance(make_provider("xiaomi-mimo"), XiaomiProvider)


def test_infer_audio_format_from_mime():
    assert xiaomi.infer_audio_format("audio/wav") == "wav"
    assert xiaomi.infer_audio_format("audio/x-wav") == "wav"
    assert xiaomi.infer_audio_format("audio/mpeg") == "mp3"
    assert xiaomi.infer_audio_format("application/octet-stream") == "wav"


def test_build_audio_data_url_uses_mime_and_base64():
    audio_b64 = base64.b64encode(b"audio").decode("ascii")

    assert (
        xiaomi.build_audio_data_url("audio/mpeg", audio_b64)
        == f"data:audio/mpeg;base64,{audio_b64}"
    )


def test_build_request_body_includes_xiaomi_chat_audio_shape(monkeypatch):
    monkeypatch.setattr(xiaomi, "get_model", lambda: "mimo-v2.5-asr")
    monkeypatch.setattr(xiaomi, "get_language", lambda: "auto")
    monkeypatch.setattr(xiaomi, "should_send_prompt", lambda: False)

    audio_b64 = base64.b64encode(b"audio").decode("ascii")
    body = xiaomi.build_request_body("audio/mpeg", audio_b64)

    assert body == {
        "model": "mimo-v2.5-asr",
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": f"data:audio/mpeg;base64,{audio_b64}",
                        },
                    },
                ],
            }
        ],
        "asr_options": {
            "language": "auto",
        },
    }


def test_build_request_body_can_experimentally_include_prompt(monkeypatch):
    monkeypatch.setattr(xiaomi, "get_model", lambda: "mimo-v2.5-asr")
    monkeypatch.setattr(xiaomi, "get_language", lambda: "zh")
    monkeypatch.setattr(xiaomi, "should_send_prompt", lambda: True)
    monkeypatch.setattr(
        xiaomi, "ps_get_prompt", lambda: "Use this vocabulary: CapsWriter."
    )

    audio_b64 = base64.b64encode(b"audio").decode("ascii")
    body = xiaomi.build_request_body("audio/wav", audio_b64)

    assert body["messages"][0]["content"][0] == {
        "type": "text",
        "text": "Use this vocabulary: CapsWriter.",
    }
    assert body["messages"][0]["content"][1]["type"] == "input_audio"
    assert body["asr_options"] == {"language": "zh"}


def test_get_language_falls_back_to_auto(monkeypatch):
    monkeypatch.setattr(xiaomi, "ps_get_str", lambda *args, **kwargs: "ja")

    assert xiaomi.get_language() == "auto"


def test_build_headers_defaults_to_api_key(monkeypatch):
    monkeypatch.setattr(xiaomi, "get_auth_header_mode", lambda: "api-key")

    headers = xiaomi.build_headers("sk-test")

    assert headers["api-key"] == "sk-test"
    assert "Authorization" not in headers
    assert headers["Content-Type"] == "application/json"


def test_build_headers_supports_bearer(monkeypatch):
    monkeypatch.setattr(xiaomi, "get_auth_header_mode", lambda: "bearer")

    headers = xiaomi.build_headers("sk-test")

    assert headers["Authorization"] == "Bearer sk-test"
    assert "api-key" not in headers


def test_extract_text_reads_chat_completion_message():
    class DummyResponse:
        text = '{"choices":[{"message":{"content":"hello world"}}]}'

        def json(self):
            return {"choices": [{"message": {"content": "hello world"}}]}

    assert xiaomi._extract_text(DummyResponse()) == "hello world"
