"""Real-world correction-capture cases observed in Typora."""

import pytest

from src.tsf_ipc.context_snapshot import TsfContextSnapshot
from src.tsf_ipc.text_reconciler import IncrementalTextTracker


def _whole_range(text: str) -> TsfContextSnapshot:
    return TsfContextSnapshot(text, len(text), 0, len(text))


@pytest.mark.parametrize(
    ("committed_text", "observed_texts", "expected_text"),
    [
        pytest.param(
            "你好，这是一个简单的测试。",
            [
                "你好，这是一个简单的测试。\n\n",
                "好，这是一个简单的测试。\n\n",
                "您好，这是一个简单的测试。\n\n",
                "您好，这是一个简单的测试。\n\n\n",
            ],
            "您好，这是一个简单的测试。",
            id="2026-08-07-leading-replacement",
        ),
        pytest.param(
            "你好，我明天准备去上海。",
            [
                "你好，我明天准备去上海。\n\n",
                "你好，我明准备去上海。\n\n",
                "你好，我准备去上海。\n\n",
                "你好，我后天才准备去上海。\n\n",
                "你好，我后天才准备去上海。\n\n\n",
            ],
            "你好，我后天才准备去上海。",
            id="2026-08-07-middle-replacement",
        ),
        pytest.param(
            "我不知道该怎么处理。",
            [
                "我不知道该怎么处理。\n\n\n",
                "我不知道该怎么处理。\n\n",
                "这件事。\n\n",
                "这件事应该。\n\n",
                "这件事应该重新。\n\n",
                "这件事应该重新考虑。\n\n",
                "这件事应该重新考虑。\n\n\n",
            ],
            "这件事应该重新考虑。",
            id="2026-08-07-whole-sentence-rewrite",
        ),
        pytest.param(
            "这是一个非常简单而直接的测试。",
            [
                "这是一个非常简单而直接的测试。\n\n",
                "这是一个非测试。\n\n",
                "这是一个测试。\n\n",
                "这是一个测试。\n\n\n",
            ],
            "这是一个测试。",
            id="2026-08-07-middle-deletion",
        ),
    ],
)
def test_typora_correction_capture_case(
    committed_text: str,
    observed_texts: list[str],
    expected_text: str,
) -> None:
    tracker = IncrementalTextTracker(_whole_range(committed_text))
    actual_text = committed_text

    for observed_text in observed_texts:
        result = tracker.advance(_whole_range(observed_text))
        if result is not None:
            actual_text = result.text

    assert actual_text.strip() == expected_text
