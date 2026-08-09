import pytest

from src.tsf_ipc.context_snapshot import TsfContextSnapshot
from src.tsf_ipc.text_reconciler import IncrementalTextTracker, reconcile_tracked_text


def _snapshot(text: str, target: str) -> TsfContextSnapshot:
    start = text.index(target)
    end = start + len(target)
    return TsfContextSnapshot(text, end, start, end)


def _current(text: str) -> TsfContextSnapshot:
    return TsfContextSnapshot(text, 0, 0, 0)


def _whole_range(text: str) -> TsfContextSnapshot:
    return TsfContextSnapshot(text, len(text), 0, len(text))


def test_reconciles_replacement_at_target_start():
    baseline = _snapshot(
        "前文\n你好，这是我们的第一个测试。\n后文",
        "你好，这是我们的第一个测试。",
    )

    result = reconcile_tracked_text(
        baseline, _current("前文\n您好，这是我们的第一个测试。\n后文")
    )

    assert result is not None
    assert result.text == "您好，这是我们的第一个测试。"
    assert result.confidence > 0.9


def test_reconciles_explicit_whole_target_deletion():
    baseline = _snapshot("前文\n待删除内容。\n后文", "待删除内容。")

    result = reconcile_tracked_text(baseline, _current("前文\n\n后文"))

    assert result is not None
    assert result.text == ""


def test_rejects_unrelated_document_snapshot():
    baseline = _snapshot("前文\n目标内容。\n后文", "目标内容。")

    result = reconcile_tracked_text(baseline, _current("完全不同的文档内容"))

    assert result is None


def test_incremental_tracker_follows_repeated_rewrites():
    tracker = IncrementalTextTracker(
        _snapshot("固定前文\n第一版内容。\n固定后文", "第一版内容。")
    )

    first = tracker.advance(_current("固定前文\n第二版完全不同。\n固定后文"))
    second = tracker.advance(
        _snapshot("固定前文也被修改\n最终结果。\n固定后文", "最终结果。")
    )

    assert first is not None
    assert first.text == "第二版完全不同。"
    assert second is not None
    assert second.text == "最终结果。"


def test_incremental_tracker_does_not_advance_after_rejected_snapshot():
    baseline = _snapshot("前文\n目标文本。\n后文", "目标文本。")
    tracker = IncrementalTextTracker(baseline)

    assert tracker.advance(_current("不相关文档")) is None
    result = tracker.advance(_current("前文\n用户改写。\n后文"))

    assert result is not None
    assert result.text == "用户改写。"


def test_diff_candidate_repairs_native_range_missing_replacement_prefix():
    baseline = _snapshot("前文\n你好，测试。\n后文", "你好，测试。")
    current_text = "前文\n您好，测试。\n后文"
    native_start = current_text.index("好，测试。")
    current = TsfContextSnapshot(
        current_text,
        native_start + len("好，测试。"),
        native_start,
        native_start + len("好，测试。"),
    )

    result = reconcile_tracked_text(baseline, current)

    assert result is not None
    assert result.text == "您好，测试。"


@pytest.mark.parametrize(
    ("baseline_text", "observed_texts", "expected"),
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
            id="real-log-leading-replacement",
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
            id="real-log-middle-replacement",
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
            id="real-log-whole-sentence-rewrite",
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
            id="real-log-middle-deletion",
        ),
    ],
)
def test_replays_real_typora_edit_snapshots(
    baseline_text: str,
    observed_texts: list[str],
    expected: str,
) -> None:
    """Replay the stable snapshots captured in the 2026-08-07 manual test."""
    tracker = IncrementalTextTracker(_whole_range(baseline_text))
    latest = baseline_text

    for text in observed_texts:
        result = tracker.advance(_whole_range(text))
        if result is not None:
            latest = result.text

    assert latest.strip() == expected
