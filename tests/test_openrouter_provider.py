import base64

from src.transcribe.openrouter import openrouter_transcribe_http as openrouter
from src.transcribe.providers import OpenRouterProvider, make_provider


def test_make_provider_supports_openrouter():
    assert isinstance(make_provider("openrouter"), OpenRouterProvider)
    assert isinstance(make_provider("open-router"), OpenRouterProvider)


def test_infer_audio_format_from_mime(monkeypatch):
    monkeypatch.setattr(openrouter, "get_configured_audio_format", lambda: "auto")

    assert openrouter.infer_audio_format("audio/wav") == "wav"
    assert openrouter.infer_audio_format("audio/x-wav") == "wav"
    assert openrouter.infer_audio_format("audio/mpeg") == "mp3"
    assert openrouter.infer_audio_format("application/octet-stream") == "wav"


def test_infer_audio_format_prefers_configured_value(monkeypatch):
    monkeypatch.setattr(openrouter, "get_configured_audio_format", lambda: "flac")

    assert openrouter.infer_audio_format("audio/wav") == "flac"


def test_build_request_body_includes_openrouter_audio_shape(monkeypatch):
    monkeypatch.setattr(openrouter, "get_model", lambda: "google/chirp-3")
    monkeypatch.setattr(openrouter, "get_temperature", lambda: 0.0)
    monkeypatch.setattr(openrouter, "get_configured_audio_format", lambda: "auto")
    monkeypatch.setattr(openrouter, "should_send_prompt", lambda: False)

    audio_b64 = base64.b64encode(b"audio").decode("ascii")
    body = openrouter.build_request_body("audio/wav", audio_b64)

    assert body == {
        "model": "google/chirp-3",
        "input_audio": {
            "data": audio_b64,
            "format": "wav",
        },
        "temperature": 0.0,
    }


def test_build_request_body_sends_google_speech_v2_prompt_to_google_vertex_options(monkeypatch):
    monkeypatch.setattr(openrouter, "get_model", lambda: "google/chirp-3")
    monkeypatch.setattr(openrouter, "get_language", lambda: "zh")
    monkeypatch.setattr(openrouter, "get_temperature", lambda: 0.0)
    monkeypatch.setattr(openrouter, "get_configured_audio_format", lambda: "auto")
    monkeypatch.setattr(openrouter, "should_send_prompt", lambda: True)
    monkeypatch.setattr(openrouter, "get_prompt_provider_slug", lambda: "google-vertex")
    monkeypatch.setattr(openrouter, "get_prompt_option_shape", lambda: "google_speech_v2")
    monkeypatch.setattr(openrouter, "ps_get_prompt", lambda: "Expected vocabulary: OpenRouter, API, transcription")

    audio_b64 = base64.b64encode(b"audio").decode("ascii")
    body = openrouter.build_request_body("audio/wav", audio_b64)

    assert body["provider"] == {
        "options": {
            "google-vertex": {
                "config": {
                    "features": {
                        "customPromptConfig": {
                            "customPrompt": "Expected vocabulary: OpenRouter, API, transcription",
                        }
                    }
                }
            }
        }
    }
    assert "language" not in body


def test_build_google_speech_v2_options_omits_decoding_and_language_without_prompt():
    assert openrouter.build_google_speech_v2_options() == {}


def test_build_request_body_auto_sends_openai_transcription_prompt(monkeypatch):
    monkeypatch.setattr(openrouter, "get_model", lambda: "openai/gpt-4o-transcribe")
    monkeypatch.setattr(openrouter, "get_language", lambda: "zh")
    monkeypatch.setattr(openrouter, "get_temperature", lambda: 0.0)
    monkeypatch.setattr(openrouter, "get_configured_audio_format", lambda: "auto")
    monkeypatch.setattr(openrouter, "should_send_prompt", lambda: True)
    monkeypatch.setattr(openrouter, "get_prompt_provider_slug", lambda: "openai")
    monkeypatch.setattr(openrouter, "get_prompt_option_shape", lambda: "auto")
    monkeypatch.setattr(openrouter, "ps_get_prompt", lambda: "Recent developments around OpenAI and GPT-4.5.")

    audio_b64 = base64.b64encode(b"audio").decode("ascii")
    body = openrouter.build_request_body("audio/mpeg", audio_b64)

    assert body["provider"] == {
        "options": {
            "openai": {
                "prompt": "Recent developments around OpenAI and GPT-4.5.",
            }
        }
    }
    assert "language" not in body


def test_build_prompt_provider_options_supports_flat_prompt(monkeypatch):
    monkeypatch.setattr(openrouter, "get_prompt_option_shape", lambda: "flat")

    assert openrouter.build_prompt_provider_options("vocab") == {"prompt": "vocab"}


def test_build_headers_adds_optional_openrouter_metadata(monkeypatch):
    monkeypatch.setattr(openrouter, "get_site_url", lambda: "https://example.com")
    monkeypatch.setattr(openrouter, "get_site_name", lambda: "CapsWriter")

    headers = openrouter.build_headers("sk-test")

    assert headers["Authorization"] == "Bearer sk-test"
    assert headers["Content-Type"] == "application/json"
    assert headers["HTTP-Referer"] == "https://example.com"
    assert headers["X-OpenRouter-Title"] == "CapsWriter"


def test_build_headers_omits_empty_optional_metadata(monkeypatch):
    monkeypatch.setattr(openrouter, "get_site_url", lambda: None)
    monkeypatch.setattr(openrouter, "get_site_name", lambda: None)

    headers = openrouter.build_headers("sk-test")

    assert "HTTP-Referer" not in headers
    assert "X-OpenRouter-Title" not in headers
