import unittest
from unittest.mock import patch

import httpx

from src.polish.llm_polish import (
    _build_messages,
    _estimate_context_tokens,
    _format_textbox_context,
    _insert_textbox_position_markers,
    _truncate_textbox_context,
)
from src.polish.textbox_context import TextBoxContext


class _MockAsyncClient:
    sent_json = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url, headers=None, json=None):
        _MockAsyncClient.sent_json = json
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "润色后文本"}}]},
        )


class TextboxContextFormattingTest(unittest.TestCase):
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
            patch("src.polish.llm_polish.httpx.AsyncClient", _MockAsyncClient),
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
        self.assertIsNotNone(_MockAsyncClient.sent_json)
        self.assertEqual(
            _MockAsyncClient.sent_json.get("thinking"),
            {"type": "disabled"},
        )


if __name__ == "__main__":
    unittest.main()
