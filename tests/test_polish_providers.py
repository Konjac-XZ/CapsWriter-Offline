from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from src.polish.providers import normalize_provider_name
from src.polish.providers.base import PolishCompletionRequest, PolishProviderConfig
from src.polish.providers.openrouter import OpenRouterPolishProvider


class _FakeAsyncClient:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class _FakeStream:
    def __init__(self, chunks: list[dict[str, Any]]) -> None:
        self._chunks = chunks

    def __aiter__(self):
        self._iterator = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeChat:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def send_async(self, **kwargs: Any):
        self.calls.append(kwargs)
        if kwargs["stream"]:
            return _FakeStream(
                [
                    {"choices": [{"delta": {"content": "润色"}}]},
                    {"choices": [{"delta": {"content": "完成"}}]},
                    {"choices": []},
                ]
            )
        return {"choices": [{"message": {"content": "非流式结果"}}]}


class _FakeOpenRouter:
    instances: list["_FakeOpenRouter"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.options = kwargs
        self.chat = _FakeChat()
        self.__class__.instances.append(self)


def test_provider_name_aliases_are_backward_compatible() -> None:
    assert normalize_provider_name(None) == "openai_compatible"
    assert normalize_provider_name("openai") == "openai_compatible"
    assert normalize_provider_name("openai-compat") == "openai_compatible"
    assert normalize_provider_name("openrouter-sdk") == "openrouter"


def _patch_context_dependencies(monkeypatch: pytest.MonkeyPatch) -> None:
    import src.polish.llm_polish as llm_polish
    import src.infra.user_lexicon as user_lexicon

    monkeypatch.setattr(llm_polish, "_qwen_asr_textbox_enabled", lambda: False)
    monkeypatch.setattr(llm_polish, "get_recent_vision_context_summary", lambda: None)
    monkeypatch.setattr(llm_polish, "get_finalized_history", lambda: [])
    monkeypatch.setattr(llm_polish, "get_asr_finalized_history", lambda: [])
    monkeypatch.setattr(user_lexicon, "get_lexicon_user_message", lambda: None)


def test_legacy_polish_config_defaults_to_openai_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.polish.llm_polish as llm_polish

    _patch_context_dependencies(monkeypatch)
    monkeypatch.setattr(
        llm_polish,
        "_cfg",
        lambda: {
            "enabled": True,
            "model": "legacy-model",
            "textbox_context": {"enabled": False},
        },
    )
    monkeypatch.setattr(
        llm_polish,
        "_get_env",
        lambda name, default=None: {
            "LLM_POLISH_BASE_URL": "https://legacy.example/v1",
            "LLM_POLISH_API_KEY": "legacy-key",
        }.get(name, default),
    )

    context = llm_polish._prepare_polish_request_context()

    assert context.provider_name == "openai_compatible"
    assert context.base_url == "https://legacy.example/v1"
    assert context.api_key == "legacy-key"
    assert context.provider_options["extra_body"] == {"thinking": {"type": "disabled"}}


def test_openrouter_config_uses_provider_credentials_and_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.polish.llm_polish as llm_polish

    _patch_context_dependencies(monkeypatch)
    routing = {"only": ["google-vertex"], "allow_fallbacks": False}
    monkeypatch.setattr(
        llm_polish,
        "_cfg",
        lambda: {
            "enabled": True,
            "provider": "openrouter",
            "model": "google/gemini-2.5-flash",
            "openrouter": {
                "base_url": "https://openrouter.example/api/v1",
                "routing": routing,
            },
            "textbox_context": {"enabled": False},
        },
    )
    monkeypatch.setattr(
        llm_polish,
        "_get_env",
        lambda name, default=None: {
            "OPENROUTER_API_KEY": "openrouter-key",
            "LLM_POLISH_API_KEY": "legacy-key",
        }.get(name, default),
    )

    context = llm_polish._prepare_polish_request_context()

    assert context.provider_name == "openrouter"
    assert context.base_url == "https://openrouter.example/api/v1"
    assert context.api_key == "openrouter-key"
    assert context.provider_options["routing"] == routing


def test_openrouter_provider_uses_official_sdk_routing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.polish.providers.openrouter as openrouter_provider

    _FakeOpenRouter.instances = []
    monkeypatch.setattr(openrouter_provider, "OpenRouter", _FakeOpenRouter)
    monkeypatch.setattr(openrouter_provider.httpx, "AsyncClient", _FakeAsyncClient)
    deltas: list[str] = []
    accumulated: list[str] = []

    async def run_case() -> str | None:
        provider = OpenRouterPolishProvider(
            PolishProviderConfig(
                name="openrouter",
                api_key="or-test-key",
                base_url="https://openrouter.example/api/v1",
                timeout_s=12.5,
                options={
                    "site_url": "https://example.com/capswriter",
                    "site_name": "CapsWriter-Offline",
                    "routing": {
                        "order": ["xiaomi"],
                        "allow_fallbacks": False,
                        "require_parameters": True,
                        "data_collection": "deny",
                    },
                    "reasoning": {"enabled": False},
                },
            )
        )
        result = await provider.complete(
            PolishCompletionRequest(
                model="xiaomi/mimo-v2.5-pro",
                messages=[{"role": "user", "content": "原文"}],
                temperature=0.2,
                max_output_tokens=512,
            ),
            on_delta=deltas.append,
            on_text=accumulated.append,
        )
        await provider.close()
        return result.text

    assert asyncio.run(run_case()) == "润色完成"
    assert deltas == ["润色", "完成"]
    assert accumulated == ["润色", "润色完成"]

    sdk = _FakeOpenRouter.instances[0]
    assert sdk.options["api_key"] == "or-test-key"
    assert sdk.options["server_url"] == "https://openrouter.example/api/v1"
    assert sdk.options["http_referer"] == "https://example.com/capswriter"
    assert sdk.options["x_open_router_title"] == "CapsWriter-Offline"
    assert sdk.options["retry_config"] is None

    request = sdk.chat.calls[0]
    assert request["model"] == "xiaomi/mimo-v2.5-pro"
    assert request["max_tokens"] == 512
    assert request["provider"] == {
        "order": ["xiaomi"],
        "allow_fallbacks": False,
        "require_parameters": True,
        "data_collection": "deny",
    }
    assert request["reasoning"] == {"enabled": False}
    assert request["stream"] is True


def test_openrouter_provider_falls_back_to_nonstream(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    import src.polish.providers.openrouter as openrouter_provider

    fake_secret = "sk-" + "or-v1-test-secret-value"

    class _FailingStreamChat(_FakeChat):
        async def send_async(self, **kwargs: Any):
            self.calls.append(kwargs)
            if kwargs["stream"]:
                raise RuntimeError(f"stream unavailable with {fake_secret}")
            return {"choices": [{"message": {"content": "回退结果"}}]}

    class _FallbackOpenRouter(_FakeOpenRouter):
        def __init__(self, **kwargs: Any) -> None:
            self.options = kwargs
            self.chat = _FailingStreamChat()
            self.__class__.instances.append(self)

    _FallbackOpenRouter.instances = []
    monkeypatch.setattr(openrouter_provider, "OpenRouter", _FallbackOpenRouter)
    monkeypatch.setattr(openrouter_provider.httpx, "AsyncClient", _FakeAsyncClient)
    caplog.set_level(logging.INFO, logger="capswriter.polish.openrouter")

    async def run_case() -> str | None:
        provider = OpenRouterPolishProvider(
            PolishProviderConfig(
                name="openrouter",
                api_key="or-test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_s=10,
            )
        )
        result = await provider.complete(
            PolishCompletionRequest(
                model="openai/gpt-5-mini",
                messages=[{"role": "user", "content": "原文"}],
            )
        )
        await provider.close()
        return result.text

    assert asyncio.run(run_case()) == "回退结果"
    assert [call["stream"] for call in _FallbackOpenRouter.instances[0].chat.calls] == [
        True,
        False,
    ]
    output = capsys.readouterr().out
    assert "[LLM 润色][OpenRouter] 流式请求失败" in output
    assert "stream unavailable" in output
    assert fake_secret not in output
    assert "[REDACTED]" in output
    assert "开始非流式回退请求" in caplog.text
    assert "非流式回退请求成功" in caplog.text
    assert "request_id=" in caplog.text


def test_openrouter_provider_logs_empty_stream_fallback(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import src.polish.providers.openrouter as openrouter_provider

    class _EmptyStreamChat(_FakeChat):
        async def send_async(self, **kwargs: Any):
            self.calls.append(kwargs)
            if kwargs["stream"]:
                return _FakeStream([{"choices": []}])
            return {"choices": [{"message": {"content": "回退结果"}}]}

    class _EmptyStreamOpenRouter(_FakeOpenRouter):
        def __init__(self, **kwargs: Any) -> None:
            self.options = kwargs
            self.chat = _EmptyStreamChat()
            self.__class__.instances.append(self)

    _EmptyStreamOpenRouter.instances = []
    monkeypatch.setattr(openrouter_provider, "OpenRouter", _EmptyStreamOpenRouter)
    monkeypatch.setattr(openrouter_provider.httpx, "AsyncClient", _FakeAsyncClient)
    caplog.set_level(logging.INFO, logger="capswriter.polish.openrouter")

    async def run_case() -> str | None:
        provider = OpenRouterPolishProvider(
            PolishProviderConfig(
                name="openrouter",
                api_key="or-test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_s=10,
            )
        )
        try:
            result = await provider.complete(
                PolishCompletionRequest(
                    model="deepseek/deepseek-v3.2",
                    messages=[{"role": "user", "content": "原文"}],
                )
            )
            return result.text
        finally:
            await provider.close()

    assert asyncio.run(run_case()) == "回退结果"
    assert "流式响应未返回可用文本，将回退到非流式请求" in caplog.text
    assert "开始非流式回退请求" in caplog.text
    assert "非流式回退请求成功" in caplog.text


def test_openrouter_provider_logs_nonstream_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import src.polish.providers.openrouter as openrouter_provider

    class _AlwaysFailingChat(_FakeChat):
        async def send_async(self, **kwargs: Any):
            self.calls.append(kwargs)
            if kwargs["stream"]:
                raise RuntimeError("stream failed")
            raise RuntimeError("fallback failed")

    class _FailingOpenRouter(_FakeOpenRouter):
        def __init__(self, **kwargs: Any) -> None:
            self.options = kwargs
            self.chat = _AlwaysFailingChat()
            self.__class__.instances.append(self)

    _FailingOpenRouter.instances = []
    monkeypatch.setattr(openrouter_provider, "OpenRouter", _FailingOpenRouter)
    monkeypatch.setattr(openrouter_provider.httpx, "AsyncClient", _FakeAsyncClient)

    async def run_case() -> None:
        provider = OpenRouterPolishProvider(
            PolishProviderConfig(
                name="openrouter",
                api_key="or-test-key",
                base_url="https://openrouter.ai/api/v1",
                timeout_s=10,
            )
        )
        try:
            with pytest.raises(RuntimeError, match="fallback failed"):
                await provider.complete(
                    PolishCompletionRequest(
                        model="deepseek/deepseek-v3.2",
                        messages=[{"role": "user", "content": "原文"}],
                    )
                )
        finally:
            await provider.close()

    asyncio.run(run_case())

    output = capsys.readouterr().out
    assert "[LLM 润色][OpenRouter] 流式请求失败" in output
    assert "[LLM 润色][OpenRouter] 非流式回退请求失败" in output
    assert "fallback failed" in output
