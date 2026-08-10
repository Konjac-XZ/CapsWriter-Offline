"""Real-world correction-capture cases observed in Obsidian."""

from dataclasses import dataclass

import pytest

from src.tsf_ipc.protocol import TrackingSnapshotKind


_OBJECT = "\ufffc\ufffc"


@dataclass(frozen=True, slots=True)
class _Observation:
    revision: int
    before: str
    tracked: str
    after: str


def _replay_observations(
    make_bridge_correction_case,
    *,
    committed: str,
    baseline_before: str,
    baseline_after: str,
    observations: list[_Observation],
):
    case = make_bridge_correction_case("Obsidian.exe")
    session_id = case.commit(committed)
    case.observe(
        session_id,
        observations[0].revision - 1,
        TrackingSnapshotKind.BASELINE,
        before=baseline_before,
        tracked=committed,
        after=baseline_after,
    )
    for observation in observations:
        case.observe(
            session_id,
            observation.revision,
            TrackingSnapshotKind.CURRENT,
            before=observation.before,
            tracked=observation.tracked,
            after=observation.after,
        )
    case.settle(session_id)
    return case, session_id


@pytest.mark.parametrize(
    (
        "committed",
        "baseline_before",
        "baseline_after",
        "observations",
        "expected",
    ),
    [
        pytest.param(
            "libcoap #5，扩展 Token 长度缺少边界检查。",
            "libcoap #4 未能拒绝 Location-Path 当中的 ..（Dot-Segment）\n",
            "\n" + _OBJECT + "\n\n",
            [
                _Observation(
                    11,
                    "libcoap #4 未能拒绝 Location-Path 当中的 ..（Dot-Segment",
                    "）\nlibcoap #5，扩展 Token 长度缺少边界检",
                    "查。\n`````\n\n",
                ),
                _Observation(
                    13,
                    "libcoap #4 未能拒绝 Location-Path 当中的 ..（Dot-Segment",
                    "）\nlibcoap #5 扩展 Token 长度缺少边界检",
                    "查\n`````\n\n",
                ),
            ],
            "libcoap #5 扩展 Token 长度缺少边界检查",
            id="2026-08-10-410ca54e-left-shift-and-clipped-suffix",
        ),
        pytest.param(
            "错误地对多行标头返回 `invalid header format` 502",
            "envoy #1 错误地阻止了含有 . - + 等符号的 scheme\nenvoy #2 ",
            "\n`````\n\n",
            [
                _Observation(
                    14,
                    "envoy #1 错误地阻止了含有 . - + 等符号的 scheme\nenvoy #2 ",
                    "错误地对多行标头返回 INVALID_HEADER_FORMAT 502",
                    "\n`````\n\n",
                )
            ],
            "错误地对多行标头返回 INVALID_HEADER_FORMAT 502",
            id="2026-08-10-2c83d258-inline-code-replacement",
        ),
        pytest.param(
            "Envoy #3 调用 HTTP/2 后端时，HTTP/1.1 的 TE 和 Connection 头部未被移除",
            "envoy #2 错误地对多行标头返回 INVALID_HEADER_FORMAT 502\n",
            "\n" + _OBJECT + "\n\n",
            [
                _Observation(
                    21,
                    "envoy #2 错误地对多行标头返回 INVALID_HEADER_FORMAT 50",
                    "2\nEnvoy #3 调用 HTTP/2 后端时，HTTP/1.1 的 TE 和 Connection 头部未被",
                    "移除\n`````\n\n",
                ),
                _Observation(
                    26,
                    "envoy #2 错误地对多行标头返回 INVALID_HEADER_FORMAT 50",
                    "2\nenvoy #3 调用 HTTP/2 后端时 HTTP/1.1 的 TE 和 Connection 头部未被",
                    "移除\n\n`````\n\n",
                ),
            ],
            "envoy #3 调用 HTTP/2 后端时 HTTP/1.1 的 TE 和 Connection 头部未被移除",
            id="2026-08-10-17eab7fb-previous-line-digit-captured",
        ),
        pytest.param(
            "envoy #4 JWT 认证过滤器错误地将有效 token 空格及额外字符组成的无效 JWT 判定为通过",
            "envoy #3 调用 HTTP/2 后端时 HTTP/1.1 的 TE 和 Connection 头部未被移除\n",
            "\n" + _OBJECT + "\n\n",
            [
                _Observation(
                    19,
                    "envoy #3 调用 HTTP/2 后端时 HTTP/1.1 的 TE 和 Connection 头部未被移",
                    "除\nenvoy #4 JWT 认证过滤器错误地将有效 token 空格及额外字符组成的无效 JWT 判定为",
                    "通过\n`````\n\n",
                ),
                _Observation(
                    20,
                    "envoy #3 调用 HTTP/2 后端时 HTTP/1.1 的 TE 和 Connection 头部未被移",
                    "除\nenvoy #4 JWT 认证过滤器错误地将有效字符组成的无效 JWT 判定为",
                    "通过\n\n`````\n\n",
                ),
            ],
            "envoy #4 JWT 认证过滤器错误地将有效字符组成的无效 JWT 判定为通过",
            id="2026-08-10-32f00f62-previous-line-tail-captured",
        ),
        pytest.param(
            "Unghs #7 在遇到 CR 时，错误地终止了 HTTP 头",
            "mongoose #6 未拒绝包含非数字字符的 Content-Length 字段\n",
            "\n" + _OBJECT + "\n\n",
            [
                _Observation(
                    15,
                    "mongoose #6 未拒绝包含非数字字符的 Content-Length 字",
                    "段\nUnghs #7 在遇到 CR 时，错误地终止了 HTTP",
                    " 头\n`````\n\n",
                ),
                _Observation(
                    23,
                    "mongoose #6 未拒绝包含非数字字符的 Content-Length 字",
                    "段\nmongoose #7 在遇到 CR 时，错误地终止了 HTTP",
                    " 头\n`````\n\n",
                ),
            ],
            "mongoose #7 在遇到 CR 时，错误地终止了 HTTP 头",
            id="2026-08-10-20f7c0cd-renamed-prefix-with-two-sided-drift",
        ),
    ],
)
def test_2026_08_10_reviewed_boundary_repairs(
    make_bridge_correction_case,
    committed: str,
    baseline_before: str,
    baseline_after: str,
    observations: list[_Observation],
    expected: str,
) -> None:
    case, session_id = _replay_observations(
        make_bridge_correction_case,
        committed=committed,
        baseline_before=baseline_before,
        baseline_after=baseline_after,
        observations=observations,
    )

    assert case.updates[-1] == (session_id, expected)


def test_2026_08_10_99b1ec52_fence_and_next_item_are_not_captured(
    make_bridge_correction_case,
) -> None:
    committed = "未能拒绝特定不应携带正文的报文类型携带报文。"
    expected = "未能拒绝特定不应携带正文的报文类型携带正文"
    before = "libcoap #3 "
    observations = [
        _Observation(12, before, committed[:-1], "\n`````\n\n"),
        _Observation(13, before, committed[:-2], "\n`````\n\n"),
        _Observation(14, before, committed[:-3], "\n`````\n\n"),
        _Observation(15, before, expected, "\n`````\n\n"),
        _Observation(18, before + "未能拒绝", expected[4:] + "\n\n" + _OBJECT, "\n\n"),
        _Observation(19, before + "未能", expected[2:] + "\n\n`````", "\n\n"),
        _Observation(
            28,
            before + "未能",
            expected[2:] + "\nlibcoap #4 \n`````",
            "\n\n",
        ),
    ]
    case, session_id = _replay_observations(
        make_bridge_correction_case,
        committed=committed,
        baseline_before=before,
        baseline_after="\n`````\n\n",
        observations=observations,
    )

    assert case.updates[-1] == (session_id, expected)


def test_2026_08_10_e09b61e0_fence_is_not_part_of_ime_replacement(
    make_bridge_correction_case,
) -> None:
    committed = "错误地拒绝了未超出重放窗口且未重复接收过的 Partial For 报文。"
    expected = "错误地拒绝了未超出重放窗口且未重复接收过的 Partial Ⅳ 报文"
    before = "libcoap #7 "
    tracked = [
        committed[:-1],
        committed[:-3] + " 报文",
        committed[:-4] + " 报文",
        committed.replace("For", "").replace("。", ""),
        committed.replace("For", "/").replace("。", ""),
        committed.replace("For", "").replace("。", ""),
        expected,
    ]
    observations = [
        _Observation(revision, before, text, "\n`````\n\n")
        for revision, text in enumerate(tracked, start=17)
    ]
    observations.extend(
        [
            _Observation(27, before + "错误", expected[2:] + "\n\n" + _OBJECT, "\n\n"),
            _Observation(28, before, expected + "\n\n`````", "\n\n"),
        ]
    )
    case, session_id = _replay_observations(
        make_bridge_correction_case,
        committed=committed,
        baseline_before=before,
        baseline_after="\n`````\n\n",
        observations=observations,
    )

    assert case.updates[-1] == (session_id, expected)


def test_2026_08_10_10f0c0ae_one_character_lateral_shift_is_not_an_edit(
    make_bridge_correction_case,
) -> None:
    committed = "mongoose #3 未拒绝包含多个不同 Content-Length 值的请求"
    previous_line = "mongoose #2 忽略头字段可选空白中包含制表符的请求\n"
    case, _ = _replay_observations(
        make_bridge_correction_case,
        committed=committed,
        baseline_before=previous_line,
        baseline_after="\n" + _OBJECT + "\n\n",
        observations=[
            _Observation(
                12,
                previous_line[:-2],
                "求\n" + committed[:-2],
                "请求\n`````\n\n",
            )
        ],
    )

    assert case.updates == []


def test_2026_08_10_4cf8df57_document_switch_is_not_a_correction(
    make_bridge_correction_case,
) -> None:
    committed = "net-snmp #4 Trap 数据包的 AuthoritativeEngineTime 总是保持不变"
    before = "net-snmp #3 未在重启后持久保存 AuthoritativeEngineBoots\n"
    unrelated_document = (
        "if (session.securityEngineIDLen == 0) {\n"
        "    session.securityEngineID = generate_engine_id();\n"
        "}\n"
        "The authoritative engine time is then updated for every trap.\n"
    ) * 8
    case, _ = _replay_observations(
        make_bridge_correction_case,
        committed=committed,
        baseline_before=before,
        baseline_after="\n`````\n\n",
        observations=[
            _Observation(
                16,
                before[1:],
                committed[2:] + "\n" + _OBJECT,
                "\n\n",
            ),
            _Observation(17, "issue body\n", unrelated_document, "\ncomments"),
        ],
    )

    assert case.updates == []


def test_2026_08_10_3c81c90f_inserted_item_does_not_steal_renumbered_item(
    make_bridge_correction_case,
) -> None:
    committed = "haproxy #4 转发过程中丢失了 neverIndexed 属性"
    expected = "haproxy #5 转发过程中丢失了 neverIndexed 属性"
    previous_line = "haproxy #3 错误地规范化 Options 中为空的 URI Path\n"
    case, session_id = _replay_observations(
        make_bridge_correction_case,
        committed=committed,
        baseline_before=previous_line,
        baseline_after="\n" + _OBJECT + "\n\n",
        observations=[
            _Observation(
                38,
                previous_line[:-2],
                "h\nhaproxy #4 \n转发过程中丢失了 neverIndexed ",
                "属性\n\n`````\n\n",
            ),
            _Observation(
                49,
                previous_line[:-2],
                "h\nhaproxy #4 \nhaproxy #5 转发过程中丢失了 neverIndexed ",
                "属性\n\n`````\n\n",
            ),
        ],
    )

    assert case.updates[-1] == (session_id, expected)


def test_2026_08_10_171e8db8_adjacent_new_item_leaves_original_unchanged(
    make_bridge_correction_case,
) -> None:
    committed = "在 Transfer Coding 当中区分大小写"
    before = "envoy #6 错误地"
    case = make_bridge_correction_case("Obsidian.exe")
    session_id = case.commit(committed)
    case.observe(
        session_id,
        8,
        TrackingSnapshotKind.BASELINE,
        before=before,
        tracked=committed,
        after="\n\n`````\n\n",
    )
    case.observe(
        session_id,
        16,
        TrackingSnapshotKind.CURRENT,
        before=before + "在 ",
        tracked="Transfer Coding 当中区分大小写\nh",
        after="aproxy #1\n" + _OBJECT + "\n\n",
    )
    case.settle(session_id)
    case.observe(
        session_id,
        17,
        TrackingSnapshotKind.CURRENT,
        before=before,
        tracked=committed,
        after="\nhaproxy #1 \n`````\n\n",
    )
    case.settle(session_id)

    assert case.updates == []


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
