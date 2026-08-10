"""Real-world correction-capture cases observed in Obsidian."""

from src.tsf_ipc.protocol import TrackingSnapshotKind


_OBJECT = "\ufffc\ufffc"


def test_inline_code_rerender_then_real_edit(make_bridge_correction_case) -> None:
    case = make_bridge_correction_case("Obsidian.exe")
    committed_text = "请在 `src` 下维护项目。"
    session_id = case.commit(committed_text)

    case.observe(
        session_id,
        3,
        TrackingSnapshotKind.BASELINE,
        before="前文\n",
        tracked=committed_text,
        after="\n后文",
    )
    case.observe(
        session_id,
        4,
        TrackingSnapshotKind.CURRENT,
        before="前文\n",
        tracked="请在 \ufffc\ufffcsrc\ufffc\ufffc 下维护项目。",
        after="\n后文",
    )
    case.settle(session_id)

    assert case.updates == []

    case.observe(
        session_id,
        5,
        TrackingSnapshotKind.CURRENT,
        before="前文\n",
        tracked="请在 \ufffc\ufffclib\ufffc\ufffc 下维护项目。",
        after="\n后文",
    )
    case.settle(session_id)

    assert case.updates == [(session_id, "请在 `lib` 下维护项目。")]


def test_2026_08_10_followup_prefix_is_not_captured_after_tail_rewrite(
    make_bridge_correction_case,
) -> None:
    """Replay session 96aaaa23, including Obsidian's rendered code markers."""
    case = make_bridge_correction_case("Obsidian.exe")
    committed = (
        "顺便在这里指定一下输出规则，你应该能在这个仓库里看到 "
        "`1.md`、`2.md`、`3.md` 等等等等。"
    )
    expected = (
        "顺便在这里指定一下输出规则，你应该能在这个仓库里看到 "
        "`1.md`、`2.md`、`3.md`……。"
    )
    rendered_prefix = (
        "顺便在这里指定一下输出规则，你应该能在这个仓库里看到 "
        f"{_OBJECT}1.md{_OBJECT}、{_OBJECT}2.md{_OBJECT}、"
    )
    clipped_before = "前" * 1024
    session_id = case.commit(committed)

    case.observe(
        session_id,
        20,
        TrackingSnapshotKind.BASELINE,
        before=clipped_before,
        tracked=committed,
        after="\n",
    )

    # Revisions 21-28 are the original ordered observations.  Obsidian first
    # projects backticks as U+FFFC pairs, then the user deletes the old tail.
    rendered_observations = [
        rendered_prefix + f"{_OBJECT}3.md{_OBJECT} 等等等等。",
        rendered_prefix + f"{_OBJECT}3.md{_OBJECT} 等等等等",
        rendered_prefix + f"{_OBJECT}3.md{_OBJECT} 等等等",
        rendered_prefix + f"{_OBJECT}3.md{_OBJECT} 等等",
        rendered_prefix + f"{_OBJECT}3.md{_OBJECT} 等",
        rendered_prefix + f"{_OBJECT}3.md{_OBJECT} ",
        rendered_prefix + "`3.md`",
        rendered_prefix + f"{_OBJECT}3.md{_OBJECT}",
    ]
    for revision, tracked in enumerate(rendered_observations, start=21):
        case.observe(
            session_id,
            revision,
            TrackingSnapshotKind.CURRENT,
            before=clipped_before,
            tracked=tracked,
            after="……\n",
        )
    case.settle(session_id)

    # Forty seconds later the document contains the reviewed correction and a
    # new sentence.  The stale 53-character native range starts one character
    # early and ends after the first two characters of that new sentence.
    contaminated = "\n" + expected + "在这"
    followup = "里，我们将本脚本的输出指定为 `1.min.json`、`2.min.json`……以此类推。\n"
    case.observe(
        session_id,
        29,
        TrackingSnapshotKind.CURRENT,
        before=clipped_before,
        tracked=contaminated,
        after=followup,
    )
    case.settle(session_id)
    case.observe(
        session_id,
        31,
        TrackingSnapshotKind.CURRENT,
        before=clipped_before,
        tracked=contaminated,
        after=followup,
    )
    case.settle(session_id)

    assert case.updates[-1] == (session_id, expected)


def test_2026_08_10_tail_ime_replacement_is_captured_completely(
    make_bridge_correction_case,
) -> None:
    """Replay session d88ffa6a and the human-reviewed final tail edit."""
    case = make_bridge_correction_case("Obsidian.exe")
    committed = (
        "当然，这里的项目本身的动机是，在这两个 JSON 文件当中真的找到我们感兴趣的内容。"
        "所以你可能得迭代地编写好几轮不同的工具来完成复查。"
    )
    expected = (
        "当然，这里的项目本身的动机是在这两个 JSON 文件当中真的找到我们感兴趣的内容。"
        "所以你可能得迭代地编写好几轮不同的工具来完成筛选。"
    )
    before = "既有文档\n\n"
    session_id = case.commit(committed)

    case.observe(
        session_id,
        20,
        TrackingSnapshotKind.BASELINE,
        before=before,
        tracked=committed,
        after="\n",
    )

    # The user removes the old terminal punctuation and word one character at
    # a time.  This shrinks the inward-gravity live range to end at “完成”.
    deletion_observations = [
        committed[:-1],
        committed[:-2],
        committed[:-3],
    ]
    for revision, tracked in enumerate(deletion_observations, start=21):
        case.observe(
            session_id,
            revision,
            TrackingSnapshotKind.CURRENT,
            before=before,
            tracked=tracked,
            after="\n",
        )
    case.settle(session_id)

    tracked_prefix = committed[:-3]
    # The log records the IME preedit outside target_end as j/jm/jms/jm so and
    # then 检索.  The user-reviewed document finally contains 筛选。; the native
    # selection remains unchanged throughout.
    for suffix in ("j", "jm", "jms", "jm so", "检索", "筛选。"):
        case.observe(
            session_id,
            23,
            TrackingSnapshotKind.CURRENT,
            before=before,
            tracked=tracked_prefix,
            after=suffix + "\n\n",
        )
    case.settle(session_id)

    without_comma = tracked_prefix.replace("动机是，", "动机是", 1)
    case.observe(
        session_id,
        24,
        TrackingSnapshotKind.CURRENT,
        before=before,
        tracked=without_comma,
        after="筛选。\n\n",
    )
    case.settle(session_id)

    assert case.updates[-1] == (session_id, expected)
