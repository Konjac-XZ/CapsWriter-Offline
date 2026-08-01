from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

import httpx
from openrouter import OpenRouter

from src.polish.providers.base import (
    PolishCompletionRequest,
    PolishCompletionResult,
    PolishProviderConfig,
    PolishStreamCallback,
    invoke_callback,
)


_LOGGER = logging.getLogger("capswriter.polish.openrouter")
_MAX_ERROR_DETAIL_CHARS = 1200


def _safe_error_detail(exc: BaseException) -> str:
    """Return a bounded SDK error summary without credentials or request content."""
    parts = [f"{type(exc).__name__}: {exc}"]
    response = getattr(exc, "raw_response", None)
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, int):
        parts.append(f"status={status_code}")
    body = getattr(exc, "body", None)
    if isinstance(body, str) and body.strip() and body.strip() not in parts[0]:
        parts.append(f"response={body.strip()}")

    detail = " | ".join(parts).replace("\r", " ").replace("\n", " ")
    detail = re.sub(r"(?i)bearer\s+[A-Za-z0-9._~+\-/]+=*", "Bearer [REDACTED]", detail)
    detail = re.sub(r"(?i)sk-[A-Za-z0-9_-]{8,}", "[REDACTED]", detail)
    detail = re.sub(r"\s+", " ", detail).strip()
    if len(detail) > _MAX_ERROR_DETAIL_CHARS:
        return detail[: _MAX_ERROR_DETAIL_CHARS - 3] + "..."
    return detail


def _emit_error(message: str, exc: BaseException | None = None) -> None:
    detail = f"{message}：{_safe_error_detail(exc)}" if exc is not None else message
    _LOGGER.error(detail)
    try:
        print(detail, flush=True)
    except Exception:
        pass


def _emit_warning(message: str) -> None:
    _LOGGER.warning(message)
    try:
        print(message, flush=True)
    except Exception:
        pass


def _value(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(name, default)
    return getattr(value, name, default)


def _extract_content(value: Any) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, (list, tuple)):
        return None
    parts: list[str] = []
    for item in value:
        text = _value(item, "text")
        if isinstance(text, str):
            parts.append(text)
    return "".join(parts) or None


def _extract_delta(chunk: Any) -> str | None:
    choices = _value(chunk, "choices", [])
    if not choices:
        return None
    delta = _value(choices[0], "delta")
    return _extract_content(_value(delta, "content"))


def _extract_result_text(result: Any) -> str | None:
    choices = _value(result, "choices", [])
    if not choices:
        return None
    message = _value(choices[0], "message")
    return _extract_content(_value(message, "content"))


class OpenRouterPolishProvider:
    """OpenRouter adapter backed by OpenRouter's official Python SDK."""

    def __init__(self, config: PolishProviderConfig) -> None:
        self._config = config
        self._http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_s),
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
                keepalive_expiry=float(config.options.get("keepalive_expiry", 90.0)),
            ),
        )
        client_options: dict[str, Any] = {
            "api_key": config.api_key,
            "async_client": self._http_client,
            "timeout_ms": max(1, int(config.timeout_s * 1000)),
        }
        if config.base_url:
            client_options["server_url"] = config.base_url.rstrip("/")
        site_url = config.options.get("site_url")
        site_name = config.options.get("site_name")
        if isinstance(site_url, str) and site_url.strip():
            client_options["http_referer"] = site_url.strip()
        if isinstance(site_name, str) and site_name.strip():
            client_options["x_open_router_title"] = site_name.strip()
        try:
            self._client = OpenRouter(**client_options)
        except Exception as exc:
            _emit_error("[LLM 润色][OpenRouter] SDK 客户端初始化失败", exc)
            raise

    async def close(self) -> None:
        try:
            await self._http_client.aclose()
        except Exception:
            pass

    async def complete(
        self,
        request: PolishCompletionRequest,
        *,
        on_delta: PolishStreamCallback | None = None,
        on_text: PolishStreamCallback | None = None,
    ) -> PolishCompletionResult:
        kwargs = self._build_kwargs(request)
        try:
            stream = await self._client.chat.send_async(stream=True, **kwargs)
            current_text = ""
            async for chunk in stream:
                delta = _extract_delta(chunk)
                if not delta:
                    continue
                current_text += delta
                await invoke_callback(on_delta, delta)
                await invoke_callback(on_text, current_text)
            if current_text.strip():
                return PolishCompletionResult(current_text, 200)
        except Exception as exc:
            # Match the legacy transport behavior: retry once without streaming.
            _emit_error(
                "[LLM 润色][OpenRouter] 流式请求失败，将回退到非流式请求"
                f"（model={request.model}）",
                exc,
            )
        else:
            _emit_warning(
                "[LLM 润色][OpenRouter] 流式响应未返回可用文本，将回退到非流式请求"
                f"（model={request.model}）"
            )

        try:
            result = await self._client.chat.send_async(stream=False, **kwargs)
        except Exception as exc:
            _emit_error(
                "[LLM 润色][OpenRouter] 非流式回退请求失败"
                f"（model={request.model}）",
                exc,
            )
            raise
        text = _extract_result_text(result)
        if not text or not text.strip():
            _emit_error(
                "[LLM 润色][OpenRouter] 非流式响应未包含可用文本"
                f"（model={request.model}）"
            )
        return PolishCompletionResult(text, 200)

    def _build_kwargs(self, request: PolishCompletionRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": request.model,
            "messages": list(request.messages),
        }
        if request.temperature is not None:
            kwargs["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            # OpenRouter still exposes max_tokens for providers whose endpoint
            # metadata does not advertise the newer max_completion_tokens name.
            # With require_parameters=true, using the latter would incorrectly
            # filter out compatible endpoints such as Friendli DeepSeek V3.2.
            kwargs["max_tokens"] = request.max_output_tokens

        routing = self._config.options.get("routing")
        if isinstance(routing, Mapping) and routing:
            kwargs["provider"] = dict(routing)
        reasoning = self._config.options.get("reasoning")
        if isinstance(reasoning, Mapping) and reasoning:
            kwargs["reasoning"] = dict(reasoning)
        request_options = self._config.options.get("request_options")
        if isinstance(request_options, Mapping):
            for key, value in request_options.items():
                if key not in {"messages", "model", "stream"}:
                    kwargs[str(key)] = value
        return kwargs
