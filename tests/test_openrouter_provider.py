import asyncio
import base64
from typing import Any, cast

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
    monkeypatch.setattr(openrouter, "get_language", lambda: None)
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
        "stream": True,
        "temperature": 0.0,
    }


def test_build_request_body_sends_google_speech_v2_prompt_to_google_vertex_options(
    monkeypatch,
):
    monkeypatch.setattr(openrouter, "get_model", lambda: "google/chirp-3")
    monkeypatch.setattr(openrouter, "get_language", lambda: "zh")
    monkeypatch.setattr(openrouter, "get_temperature", lambda: 0.0)
    monkeypatch.setattr(openrouter, "get_configured_audio_format", lambda: "auto")
    monkeypatch.setattr(openrouter, "should_send_prompt", lambda: True)
    monkeypatch.setattr(openrouter, "get_prompt_provider_slug", lambda: "google-vertex")
    monkeypatch.setattr(
        openrouter, "get_prompt_option_shape", lambda: "google_speech_v2"
    )
    monkeypatch.setattr(
        openrouter,
        "ps_get_prompt",
        lambda: "Expected vocabulary: OpenRouter, API, transcription",
    )

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
    assert body["language"] == "zh"


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
    monkeypatch.setattr(
        openrouter,
        "ps_get_prompt",
        lambda: "Recent developments around OpenAI and GPT-4.5.",
    )

    audio_b64 = base64.b64encode(b"audio").decode("ascii")
    body = openrouter.build_request_body("audio/mpeg", audio_b64)

    assert body["provider"] == {
        "options": {
            "openai": {
                "prompt": "Recent developments around OpenAI and GPT-4.5.",
            }
        }
    }
    assert body["language"] == "zh"


def test_build_request_body_sends_gpt_transcribe_context_fields(monkeypatch):
    monkeypatch.setattr(openrouter, "get_model", lambda: "openai/gpt-transcribe")
    monkeypatch.setattr(openrouter, "get_temperature", lambda: None)
    monkeypatch.setattr(openrouter, "get_configured_audio_format", lambda: "auto")
    monkeypatch.setattr(openrouter, "should_send_prompt", lambda: True)
    monkeypatch.setattr(openrouter, "get_prompt_provider_slug", lambda: "openai")
    monkeypatch.setattr(openrouter, "get_prompt_option_shape", lambda: "auto")
    monkeypatch.setattr(
        openrouter, "ps_get_context_prompt", lambda: "A coding meeting."
    )
    monkeypatch.setattr(
        openrouter, "load_words", lambda: ["OpenRouter", "API", "bad<term>"]
    )
    monkeypatch.setattr(
        openrouter,
        "ps_get_value",
        lambda key, env=None, default=None: ["zh-cn", "en"],
    )

    body = openrouter.build_request_body("audio/wav", "YXVkaW8=")

    assert body["provider"] == {
        "options": {
            "openai": {
                "prompt": "A coding meeting.",
                "keywords": ["OpenRouter", "API"],
                "languages": ["zh-cn", "en"],
            }
        }
    }
    assert "language" not in body


def test_extract_stream_text_supports_openai_transcript_events():
    assert openrouter._extract_stream_text(
        {"type": "transcript.text.delta", "delta": "hello"}
    ) == ("hello", True)
    assert openrouter._extract_stream_text(
        {"type": "transcript.text.done", "text": "hello world"}
    ) == ("hello world", False)


def test_emit_transcript_delta_marks_append_only_stream(monkeypatch):
    class _Queue:
        def __init__(self):
            self.messages = []

        async def put(self, message):
            self.messages.append(message)

    queue = _Queue()
    monkeypatch.setattr(openrouter.Cosmic, "queue_out", queue, raising=False)

    asyncio.run(openrouter._emit_transcript_delta("task", "hello", 1.0, 2.0, 3.0))

    assert queue.messages == [
        {
            "task_id": "task",
            "is_final": False,
            "text": "hello",
            "time_start": 1.0,
            "time_stop": 2.0,
            "time_submit": 3.0,
            "time_complete": queue.messages[0]["time_complete"],
            "source": "mic",
            "is_transcript_delta": True,
            "stream": True,
        }
    ]


def test_stream_transcribe_emits_the_complete_accumulated_delta_text(monkeypatch):
    class _Queue:
        def __init__(self):
            self.messages = []

        async def put(self, message):
            self.messages.append(message)

    class _Response:
        status_code = 200
        http_version = "HTTP/2"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aiter_lines(self):
            for line in (
                'data: {"type":"transcript.text.delta","delta":"hello"}',
                'data: {"type":"transcript.text.delta","delta":" world"}',
                'data: {"type":"transcript.text.done","text":"hello world"}',
                "data: [DONE]",
            ):
                yield line

    class _Client:
        def stream(self, method, url, headers=None, json=None):
            return _Response()

    queue = _Queue()
    monkeypatch.setattr(openrouter.Cosmic, "queue_out", queue, raising=False)

    result = asyncio.run(
        openrouter.stream_transcribe(
            cast(Any, _Client()),
            "https://example.test",
            {},
            {"stream": True},
            "task",
            1.0,
            2.0,
        )
    )

    assert result[0] == "hello world"
    assert result[1] == 200
    assert result[4] is True
    assert queue.messages[-1]["text"] == "hello world"


def test_build_prompt_provider_options_supports_flat_prompt(monkeypatch):
    monkeypatch.setattr(openrouter, "get_prompt_option_shape", lambda: "flat")

    assert openrouter.build_prompt_provider_options("vocab") == {"prompt": "vocab"}


def test_safe_request_summary_does_not_log_context_or_keywords():
    summary = openrouter._safe_request_summary(
        {
            "model": "openai/gpt-transcribe",
            "provider": {
                "options": {
                    "openai": {
                        "prompt": "private meeting context",
                        "keywords": ["secret-product", "AC-42"],
                        "languages": ["zh-cn", "en"],
                    }
                }
            },
        }
    )

    assert summary["provider"] == {
        "options": {
            "openai": {
                "keys": ["keywords", "languages", "prompt"],
                "keywords_count": 2,
            }
        }
    }
    assert "private meeting context" not in str(summary)
    assert "secret-product" not in str(summary)


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
