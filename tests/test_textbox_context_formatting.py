import unittest
from unittest.mock import patch

import httpx

from src.polish.llm_polish import (
    _build_messages,
    _estimate_context_tokens,
    _format_textbox_context,
    _insert_textbox_position_markers,
    remove_textbox_duplicate_prefix,
    _truncate_textbox_context,
)
from src.polish.textbox_context import TextBoxContext


class _MockAsyncClient:
    created_count = 0
    closed_count = 0
    sent_json = None
    sent_stream_json = None
    stream_lines = [
        'data: {"choices":[{"delta":{"content":"润色"}}]}',
        'data: {"choices":[{"delta":{"content":"后文本"}}]}',
        "data: [DONE]",
    ]
    stream_status_code = 200
    stream_error = None
    post_response = httpx.Response(
        200,
        json={"choices": [{"message": {"content": "润色后文本"}}]},
    )

    def __init__(self, *args, **kwargs) -> None:
        _MockAsyncClient.created_count += 1

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def aclose(self) -> None:
        _MockAsyncClient.closed_count += 1

    async def post(self, url, headers=None, json=None):
        _MockAsyncClient.sent_json = json
        return _MockAsyncClient.post_response

    def stream(self, method, url, headers=None, json=None):
        _MockAsyncClient.sent_stream_json = json
        return _MockStreamResponse(
            _MockAsyncClient.stream_status_code,
            _MockAsyncClient.stream_lines,
            _MockAsyncClient.stream_error,
        )


class _MockStreamResponse:
    def __init__(self, status_code, lines, error=None) -> None:
        self.status_code = status_code
        self._lines = lines
        self._error = error
        self.headers = {"content-type": "text/event-stream"}

    async def __aenter__(self):
        if self._error:
            raise self._error
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def aiter_bytes(self):
        for line in self._lines:
            yield f"{line}\n\n".encode("utf-8")

    async def aiter_text(self):
        for line in self._lines:
            yield f"{line}\n\n"


class TextboxContextFormattingTest(unittest.TestCase):
    def test_remove_textbox_duplicate_prefix_strips_repeated_context_prefix(self) -> None:
        captured = TextBoxContext(
            text="这比我们一开始讨论的那锅稀汤",
            source="uia_text",
            caret_offset=4,
            selection_start=4,
            selection_end=4,
        )

        result = remove_textbox_duplicate_prefix(
            "这比我们一开始讨论的那锅稀汤好太多了。",
            captured,
        )

        self.assertEqual(result, "一开始讨论的那锅稀汤好太多了。")

    def test_remove_textbox_duplicate_prefix_leaves_non_overlapping_text(self) -> None:
        captured = TextBoxContext(
            text="这比我们",
            source="uia_text",
            caret_offset=4,
            selection_start=4,
            selection_end=4,
        )

        result = remove_textbox_duplicate_prefix("完全不同的回答", captured)

        self.assertEqual(result, "完全不同的回答")

    def test_inserts_caret_marker_at_offset(self) -> None:
        text, marker_offset = _insert_textbox_position_markers(
            "abcdef",
            caret_offset=3,
        )

        self.assertEqual(text, "abc<|caret|>def")
        self.assertEqual(marker_offset, 3)

    def test_inserts_selection_markers_and_caret_at_selection_end(self) -> None:
        text, marker_offset = _insert_textbox_position_markers(
            "abcdef",
            caret_offset=4,
            selection_start=2,
            selection_end=4,
        )

        self.assertEqual(
            text,
            "ab<|selection_start|>cd<|selection_end|><|caret|>ef",
        )
        self.assertEqual(marker_offset, len("ab<|selection_start|>cd<|selection_end|>"))

    def test_caret_aware_truncation_keeps_marker_near_center(self) -> None:
        captured = TextBoxContext(
            text=("A" * 200) + "BBBBBBBBBB" + ("C" * 200),
            source="uia_text",
            caret_offset=205,
            selection_start=205,
            selection_end=205,
        )

        with patch(
            "src.polish.llm_polish._cfg",
            return_value={"textbox_context": {"truncate_marker": "[...]"}},
        ):
            text, truncated = _format_textbox_context(captured, 120)

        self.assertTrue(truncated)
        self.assertLessEqual(len(text), 120)
        self.assertIn("<|caret|>", text)
        self.assertIn("BBBBB", text)
        self.assertIn("[...]", text)

    def test_caret_aware_truncation_respects_token_budget(self) -> None:
        captured = TextBoxContext(
            text=("一" * 800) + "光标附近主题" + ("二" * 800),
            source="uia_text",
            caret_offset=806,
            selection_start=806,
            selection_end=806,
        )

        with patch(
            "src.polish.llm_polish._cfg",
            return_value={"textbox_context": {"truncate_marker": "[...]"}},
        ):
            text, truncated = _format_textbox_context(captured, 2400, 120)

        self.assertTrue(truncated)
        self.assertLessEqual(_estimate_context_tokens(text), 120)
        self.assertIn("<|caret|>", text)
        self.assertIn("光标附近", text)

    def test_caret_at_start_uses_plain_context_without_marker(self) -> None:
        captured = TextBoxContext(
            text="abcdef",
            source="uia_text",
            caret_offset=0,
            selection_start=0,
            selection_end=0,
        )

        text, truncated = _format_textbox_context(captured, 100)

        self.assertFalse(truncated)
        self.assertEqual(text, "abcdef")
        self.assertNotIn("<|caret|>", text)

    def test_plain_context_uses_legacy_prompt_without_caret_language(self) -> None:
        with patch("src.infra.user_lexicon.get_lexicon_user_message", return_value=None):
            messages = _build_messages(
                "",
                "ASR",
                "abcdef",
                None,
                [],
                textbox_context_has_position=False,
            )

        textbox_message = messages[0]["content"]
        self.assertIn("当前文本框中的上下文片段", textbox_message)
        self.assertIn("当前话题", textbox_message)
        self.assertNotIn("表示用户当前输入光标位置", textbox_message)

    def test_without_caret_uses_existing_head_tail_truncation(self) -> None:
        with patch(
            "src.polish.llm_polish._cfg",
            return_value={"textbox_context": {"truncate_marker": "[...]"}},
        ):
            text, truncated = _truncate_textbox_context("0123456789" * 10, 50)

        self.assertTrue(truncated)
        self.assertEqual(text, "0123456789012345[...]12345678901234567890123456789")

    def test_plain_truncation_respects_token_budget(self) -> None:
        with patch(
            "src.polish.llm_polish._cfg",
            return_value={"textbox_context": {"truncate_marker": "[...]"}},
        ):
            text, truncated = _truncate_textbox_context("测试" * 500, 2000, 80)

        self.assertTrue(truncated)
        self.assertLessEqual(_estimate_context_tokens(text), 80)

    def test_polish_request_disables_thinking(self) -> None:
        import asyncio

        from src.polish.llm_polish import polish_text

        _MockAsyncClient.sent_json = None
        _MockAsyncClient.sent_stream_json = None
        _MockAsyncClient.stream_lines = [
            'data: {"choices":[{"delta":{"content":"润色"}}]}',
            'data: {"choices":[{"delta":{"content":"后文本"}}]}',
            "data: [DONE]",
        ]
        _MockAsyncClient.stream_status_code = 200
        _MockAsyncClient.stream_error = None
        _MockAsyncClient.post_response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "润色后文本"}}]},
        )
        cfg = {
            "enabled": True,
            "model": "test-model",
            "timeout": 1,
            "prompt": "",
            "textbox_context": {"enabled": False},
            "smart_quotes": {"enabled": False},
        }

        with (
            patch("src.polish.llm_polish._cfg", return_value=cfg),
            patch("src.polish.llm_polish._get_env") as get_env,
            patch("src.polish.providers.openai_compatible.httpx.AsyncClient", _MockAsyncClient),
            patch("src.polish.llm_polish.get_recent_vision_context_summary", return_value=None),
            patch("src.polish.llm_polish.get_finalized_history", return_value=[]),
            patch("src.infra.user_lexicon.get_lexicon_user_message", return_value=None),
        ):
            get_env.side_effect = lambda name, default=None: {
                "LLM_POLISH_BASE_URL": "https://example.test",
                "LLM_POLISH_API_KEY": "test-key",
            }.get(name, default)

            result = asyncio.run(polish_text("原文"))

        self.assertEqual(result, "润色后文本")
        self.assertIsNotNone(_MockAsyncClient.sent_stream_json)
        self.assertIsNone(_MockAsyncClient.sent_json)
        self.assertTrue(_MockAsyncClient.sent_stream_json.get("stream"))
        self.assertEqual(
            _MockAsyncClient.sent_stream_json.get("thinking"),
            {"type": "disabled"},
        )

    def test_polish_text_keeps_duplicate_prefix_while_removal_is_disabled(self) -> None:
        import asyncio

        from src.polish.llm_polish import polish_text

        _MockAsyncClient.sent_json = None
        _MockAsyncClient.sent_stream_json = None
        _MockAsyncClient.stream_lines = [
            'data: {"choices":[{"delta":{"content":"这比我们"}}]}',
            'data: {"choices":[{"delta":{"content":"一开始讨论的那锅稀汤好太多了。"}}]}',
            "data: [DONE]",
        ]
        _MockAsyncClient.stream_status_code = 200
        _MockAsyncClient.stream_error = None
        cfg = {
            "enabled": True,
            "model": "test-model",
            "timeout": 1,
            "prompt": "",
            "textbox_context": {"enabled": True, "max_chars": 200, "max_tokens": 200},
            "smart_quotes": {"enabled": False},
        }
        captured = TextBoxContext(
            text="这比我们",
            source="uia_text",
            caret_offset=4,
            selection_start=4,
            selection_end=4,
        )

        with (
            patch("src.polish.llm_polish._cfg", return_value=cfg),
            patch("src.polish.llm_polish._get_env") as get_env,
            patch("src.polish.providers.openai_compatible.httpx.AsyncClient", _MockAsyncClient),
            patch("src.polish.llm_polish.get_recent_vision_context_summary", return_value=None),
            patch("src.polish.llm_polish.get_finalized_history", return_value=[]),
            patch("src.polish.llm_polish.get_active_textbox_context", return_value=captured),
            patch("src.infra.user_lexicon.get_lexicon_user_message", return_value=None),
        ):
            get_env.side_effect = lambda name, default=None: {
                "LLM_POLISH_BASE_URL": "https://example.test",
                "LLM_POLISH_API_KEY": "test-key",
            }.get(name, default)

            result = asyncio.run(polish_text("一开始讨论的那锅稀汤好太多了。"))

        self.assertEqual(result, "这比我们一开始讨论的那锅稀汤好太多了。")

    def test_polish_stream_callbacks_receive_delta_and_accumulated_text(self) -> None:
        import asyncio

        from src.polish.llm_polish import polish_text

        _MockAsyncClient.sent_json = None
        _MockAsyncClient.sent_stream_json = None
        _MockAsyncClient.stream_lines = [
            'data: {"choices":[{"delta":{"content":"润色"}}]}',
            'data: {"choices":[{"delta":{"content":"后文本"}}]}',
            "data: [DONE]",
        ]
        _MockAsyncClient.stream_status_code = 200
        _MockAsyncClient.stream_error = None
        cfg = {
            "enabled": True,
            "model": "test-model",
            "timeout": 1,
            "prompt": "",
            "textbox_context": {"enabled": False},
            "smart_quotes": {"enabled": False},
        }
        deltas = []
        accumulated = []

        with (
            patch("src.polish.llm_polish._cfg", return_value=cfg),
            patch("src.polish.llm_polish._get_env") as get_env,
            patch("src.polish.providers.openai_compatible.httpx.AsyncClient", _MockAsyncClient),
            patch("src.polish.llm_polish.get_recent_vision_context_summary", return_value=None),
            patch("src.polish.llm_polish.get_finalized_history", return_value=[]),
            patch("src.infra.user_lexicon.get_lexicon_user_message", return_value=None),
        ):
            get_env.side_effect = lambda name, default=None: {
                "LLM_POLISH_BASE_URL": "https://example.test",
                "LLM_POLISH_API_KEY": "test-key",
            }.get(name, default)

            result = asyncio.run(
                polish_text(
                    "原文",
                    on_delta=deltas.append,
                    on_text=accumulated.append,
                )
            )

        self.assertEqual(result, "润色后文本")
        self.assertEqual(deltas, ["润色", "后文本"])
        self.assertEqual(accumulated, ["润色", "润色后文本"])

    def test_polish_falls_back_to_nonstream_when_stream_fails(self) -> None:
        import asyncio

        from src.polish.llm_polish import polish_text

        _MockAsyncClient.sent_json = None
        _MockAsyncClient.sent_stream_json = None
        _MockAsyncClient.stream_lines = []
        _MockAsyncClient.stream_status_code = 400
        _MockAsyncClient.stream_error = None
        _MockAsyncClient.post_response = httpx.Response(
            200,
            json={"choices": [{"message": {"content": "回退文本"}}]},
        )
        cfg = {
            "enabled": True,
            "model": "test-model",
            "timeout": 1,
            "prompt": "",
            "textbox_context": {"enabled": False},
            "smart_quotes": {"enabled": False},
        }

        with (
            patch("src.polish.llm_polish._cfg", return_value=cfg),
            patch("src.polish.llm_polish._get_env") as get_env,
            patch("src.polish.providers.openai_compatible.httpx.AsyncClient", _MockAsyncClient),
            patch("src.polish.llm_polish.get_recent_vision_context_summary", return_value=None),
            patch("src.polish.llm_polish.get_finalized_history", return_value=[]),
            patch("src.infra.user_lexicon.get_lexicon_user_message", return_value=None),
        ):
            get_env.side_effect = lambda name, default=None: {
                "LLM_POLISH_BASE_URL": "https://example.test",
                "LLM_POLISH_API_KEY": "test-key",
            }.get(name, default)

            result = asyncio.run(polish_text("原文"))

        self.assertEqual(result, "回退文本")
        self.assertIsNotNone(_MockAsyncClient.sent_stream_json)
        self.assertTrue(_MockAsyncClient.sent_stream_json.get("stream"))
        self.assertIsNotNone(_MockAsyncClient.sent_json)
        self.assertFalse(_MockAsyncClient.sent_json.get("stream"))

    def test_polish_stream_accepts_simple_delta_shape(self) -> None:
        import asyncio

        from src.polish.llm_polish import polish_text

        _MockAsyncClient.sent_json = None
        _MockAsyncClient.sent_stream_json = None
        _MockAsyncClient.stream_lines = [
            'data: {"delta":"片段"}',
            'data: {"delta":"文本"}',
            "data: [DONE]",
        ]
        _MockAsyncClient.stream_status_code = 200
        _MockAsyncClient.stream_error = None
        cfg = {
            "enabled": True,
            "model": "test-model",
            "timeout": 1,
            "prompt": "",
            "textbox_context": {"enabled": False},
            "smart_quotes": {"enabled": False},
        }

        with (
            patch("src.polish.llm_polish._cfg", return_value=cfg),
            patch("src.polish.llm_polish._get_env") as get_env,
            patch("src.polish.providers.openai_compatible.httpx.AsyncClient", _MockAsyncClient),
            patch("src.polish.llm_polish.get_recent_vision_context_summary", return_value=None),
            patch("src.polish.llm_polish.get_finalized_history", return_value=[]),
            patch("src.infra.user_lexicon.get_lexicon_user_message", return_value=None),
        ):
            get_env.side_effect = lambda name, default=None: {
                "LLM_POLISH_BASE_URL": "https://example.test",
                "LLM_POLISH_API_KEY": "test-key",
            }.get(name, default)

            result = asyncio.run(polish_text("原文"))

        self.assertEqual(result, "片段文本")

    def test_polish_stream_ignores_usage_only_chunk(self) -> None:
        import asyncio

        from src.polish.llm_polish import polish_text

        _MockAsyncClient.sent_json = None
        _MockAsyncClient.sent_stream_json = None
        _MockAsyncClient.stream_lines = [
            'data: {"choices":[{"delta":{"content":"润色后文本"}}]}',
            (
                'data: {"id":"chunk-id","object":"chat.completion.chunk",'
                '"choices":[],"usage":{"total_tokens":123}}'
            ),
            "data: [DONE]",
        ]
        _MockAsyncClient.stream_status_code = 200
        _MockAsyncClient.stream_error = None
        cfg = {
            "enabled": True,
            "model": "test-model",
            "timeout": 1,
            "prompt": "",
            "textbox_context": {"enabled": False},
            "smart_quotes": {"enabled": False},
        }

        with (
            patch("src.polish.llm_polish._cfg", return_value=cfg),
            patch("src.polish.llm_polish._get_env") as get_env,
            patch("src.polish.providers.openai_compatible.httpx.AsyncClient", _MockAsyncClient),
            patch("src.polish.llm_polish.get_recent_vision_context_summary", return_value=None),
            patch("src.polish.llm_polish.get_finalized_history", return_value=[]),
            patch("src.infra.user_lexicon.get_lexicon_user_message", return_value=None),
        ):
            get_env.side_effect = lambda name, default=None: {
                "LLM_POLISH_BASE_URL": "https://example.test",
                "LLM_POLISH_API_KEY": "test-key",
            }.get(name, default)

            result = asyncio.run(polish_text("原文"))

        self.assertEqual(result, "润色后文本")

    def test_polish_reuses_http_client_between_requests(self) -> None:
        import asyncio

        from src.polish.llm_polish import close_polish_http_client, polish_text

        asyncio.run(close_polish_http_client())

        async def run_case() -> tuple[str, str]:
            first = await polish_text("原文一")
            second = await polish_text("原文二")
            await close_polish_http_client()
            return first, second

        _MockAsyncClient.created_count = 0
        _MockAsyncClient.closed_count = 0
        _MockAsyncClient.sent_json = None
        _MockAsyncClient.sent_stream_json = None
        _MockAsyncClient.stream_lines = [
            'data: {"choices":[{"delta":{"content":"润色后文本"}}]}',
            "data: [DONE]",
        ]
        _MockAsyncClient.stream_status_code = 200
        _MockAsyncClient.stream_error = None
        cfg = {
            "enabled": True,
            "model": "test-model",
            "timeout": 1,
            "prompt": "",
            "textbox_context": {"enabled": False},
            "smart_quotes": {"enabled": False},
        }

        with (
            patch("src.polish.llm_polish._cfg", return_value=cfg),
            patch("src.polish.llm_polish._get_env") as get_env,
            patch("src.polish.providers.openai_compatible.httpx.AsyncClient", _MockAsyncClient),
            patch("src.polish.llm_polish.get_recent_vision_context_summary", return_value=None),
            patch("src.polish.llm_polish.get_finalized_history", return_value=[]),
            patch("src.infra.user_lexicon.get_lexicon_user_message", return_value=None),
        ):
            get_env.side_effect = lambda name, default=None: {
                "LLM_POLISH_BASE_URL": "https://example.test",
                "LLM_POLISH_API_KEY": "test-key",
            }.get(name, default)

            first, second = asyncio.run(run_case())

        self.assertEqual(first, "润色后文本")
        self.assertEqual(second, "润色后文本")
        self.assertEqual(_MockAsyncClient.created_count, 1)
        self.assertEqual(_MockAsyncClient.closed_count, 1)


if __name__ == "__main__":
    unittest.main()
