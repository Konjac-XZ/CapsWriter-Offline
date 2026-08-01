from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

import httpx
from httpx_sse import aconnect_sse

from src.infra.response_parse import extract_text_from_body
from src.polish.providers.base import (
    PolishCompletionRequest,
    PolishCompletionResult,
    PolishProviderConfig,
    PolishStreamCallback,
    invoke_callback,
)


def build_chat_completions_url(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/chat"):
        return f"{base}/completions"
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _extract_stream_text(obj: Any) -> tuple[str | None, bool]:
    if isinstance(obj, str):
        return obj, True
    if not isinstance(obj, dict):
        return None, False

    if isinstance(obj.get("delta"), str):
        return obj["delta"], True
    if isinstance(obj.get("text"), str):
        return obj["text"], False

    try:
        delta = obj["choices"][0]["delta"].get("content")
        if isinstance(delta, str):
            return delta, True
    except Exception:
        pass

    if isinstance(obj.get("choices"), list):
        return None, False
    if obj.get("object") == "chat.completion.chunk":
        return None, False

    text = extract_text_from_body(json.dumps(obj, ensure_ascii=False))
    if isinstance(text, str) and text:
        return text, False
    return None, False


class OpenAICompatiblePolishProvider:
    def __init__(self, config: PolishProviderConfig) -> None:
        self._config = config
        http2 = bool(config.options.get("http2", True))
        keepalive_expiry = float(config.options.get("keepalive_expiry", 90.0))
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(config.timeout_s),
            http2=http2,
            limits=httpx.Limits(
                max_keepalive_connections=5,
                max_connections=10,
                keepalive_expiry=keepalive_expiry,
            ),
        )

    async def close(self) -> None:
        try:
            await self._client.aclose()
        except Exception:
            pass

    async def complete(
        self,
        request: PolishCompletionRequest,
        *,
        on_delta: PolishStreamCallback | None = None,
        on_text: PolishStreamCallback | None = None,
    ) -> PolishCompletionResult:
        body = self._build_body(request)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        url = build_chat_completions_url(self._config.base_url)

        try:
            result = await self._stream(
                url,
                headers,
                body,
                on_delta=on_delta,
                on_text=on_text,
            )
        except Exception:
            result = PolishCompletionResult(None, 0)
        if result.text is not None:
            return result
        return await self._nonstream(url, headers, body)

    def _build_body(self, request: PolishCompletionRequest) -> dict[str, Any]:
        extra_body = self._config.options.get("extra_body", {})
        body = dict(extra_body) if isinstance(extra_body, Mapping) else {}
        body.update(
            {
                "model": request.model,
                "messages": list(request.messages),
                "stream": True,
            }
        )
        if request.temperature is not None:
            body["temperature"] = request.temperature
        if request.max_output_tokens is not None:
            body["max_tokens"] = request.max_output_tokens
        return body

    async def _stream(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
        *,
        on_delta: PolishStreamCallback | None,
        on_text: PolishStreamCallback | None,
    ) -> PolishCompletionResult:
        streamed_body = dict(body)
        streamed_body["stream"] = True
        current_text = ""

        async with aconnect_sse(
            self._client,
            "POST",
            url,
            headers=headers,
            json=streamed_body,
        ) as event_source:
            status_code = event_source.response.status_code
            if status_code >= 400:
                return PolishCompletionResult(None, status_code)

            async for event in event_source.aiter_sse():
                data = event.data.strip()
                if data in ("[DONE]", "DONE"):
                    break
                try:
                    obj = json.loads(data)
                except Exception:
                    text_part = extract_text_from_body(data)
                    is_delta = True
                else:
                    text_part, is_delta = _extract_stream_text(obj)

                if not isinstance(text_part, str) or not text_part:
                    continue
                if is_delta:
                    current_text += text_part
                    delta = text_part
                else:
                    delta = (
                        text_part[len(current_text) :]
                        if text_part.startswith(current_text)
                        else text_part
                    )
                    current_text = text_part
                await invoke_callback(on_delta, delta)
                await invoke_callback(on_text, current_text)

        text = current_text if current_text.strip() else None
        return PolishCompletionResult(text, status_code)

    async def _nonstream(
        self,
        url: str,
        headers: dict[str, str],
        body: dict[str, Any],
    ) -> PolishCompletionResult:
        nonstream_body = dict(body)
        nonstream_body["stream"] = False
        response = await self._client.post(url, headers=headers, json=nonstream_body)
        if response.status_code >= 400:
            return PolishCompletionResult(None, response.status_code)
        try:
            body_text = json.dumps(response.json(), ensure_ascii=False)
        except Exception:
            body_text = response.text
        text = extract_text_from_body(body_text)
        if not isinstance(text, str) or not text.strip():
            text = None
        return PolishCompletionResult(text, response.status_code)
