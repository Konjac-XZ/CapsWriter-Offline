import pytest

from src.tsf_ipc.host_processor import (
    ChatGptHostProcessor,
    DefaultHostProcessor,
    HostApplication,
    HostProcessorRegistry,
    ObsidianHostProcessor,
    RevisionDecision,
)
from src.tsf_ipc.protocol import CompositionStyle


@pytest.mark.parametrize("process_name", ["ChatGPT.exe", "chatgpt.EXE"])
def test_chatgpt_processor_matches_desktop_process_case_insensitively(process_name):
    processor = ChatGptHostProcessor()

    assert processor.matches(HostApplication(1234, process_name)) is True


@pytest.mark.parametrize("text", ["第一段\n第二段", "第一段\r第二段"])
def test_chatgpt_processor_defers_any_multiline_revision(text):
    processor = ChatGptHostProcessor()

    assert (
        processor.process_revision(text, CompositionStyle.POLISHING)
        is RevisionDecision.DEFER_FINAL
    )
    assert processor.trust_external_termination_rollback is False


def test_chatgpt_processor_keeps_single_line_revision_streaming():
    processor = ChatGptHostProcessor()

    assert (
        processor.process_revision("保持单行", CompositionStyle.POLISHING)
        is RevisionDecision.APPLY
    )


@pytest.mark.parametrize(
    "host",
    [HostApplication(), HostApplication(1234, None), HostApplication(1234, "Code.exe")],
)
def test_registry_uses_default_processor_for_unknown_hosts(host):
    default = DefaultHostProcessor()
    registry = HostProcessorRegistry(default=default)

    assert registry.resolve(host) is default
    assert default.trust_external_termination_rollback is True


@pytest.mark.parametrize("process_name", ["Obsidian.exe", "OBSIDIAN.EXE"])
def test_obsidian_processor_matches_process_case_insensitively(process_name):
    processor = ObsidianHostProcessor()
    resolved = HostProcessorRegistry().resolve(HostApplication(1234, process_name))

    assert processor.matches(HostApplication(1234, process_name)) is True
    assert resolved.name == "obsidian"


@pytest.mark.parametrize(
    ("previous", "observed", "expected"),
    [
        pytest.param(
            "请在 `src` 下维护项目。",
            "请在 \ufffc\ufffcsrc\ufffc\ufffc 下维护项目。",
            "请在 `src` 下维护项目。",
            id="representation-only",
        ),
        pytest.param(
            "读取 `data` 并写入 `src`。",
            "读取 \ufffc\ufffcdata\ufffc\ufffc 并写入 \ufffc\ufffcsrc\ufffc\ufffc。",
            "读取 `data` 并写入 `src`。",
            id="multiple-inline-code-spans",
        ),
        pytest.param(
            "请在 `src` 下维护项目。",
            "请在 \ufffc\ufffclib\ufffc\ufffc 下维护项目。",
            "请在 `lib` 下维护项目。",
            id="real-edit-inside-inline-code",
        ),
        pytest.param(
            "请在 `src` 下维护项目，并完成全部任务。",
            "请在 \ufffc\ufffcsrc\ufffc\ufffc 下维护项目。",
            "请在 `src` 下维护项目。",
            id="real-edit-outside-inline-code",
        ),
        pytest.param(
            "请在 `src` 下维护项目。",
            "请在 src 下维护项目。",
            "请在 src 下维护项目。",
            id="removed-inline-code-delimiters",
        ),
        pytest.param(
            "运行 ```demo```。",
            "运行 \ufffc\ufffc\ufffcdemo\ufffc\ufffc\ufffc。",
            "运行 \ufffc\ufffc\ufffcdemo\ufffc\ufffc\ufffc。",
            id="fenced-marker-run-is-not-rewritten",
        ),
        pytest.param(
            "请在 `src` 下维护项目。",
            "请在 \ufffc\ufffcsrc\ufffc\ufffc 下维护项目。附件 \ufffc\ufffc",
            "请在 \ufffc\ufffcsrc\ufffc\ufffc 下维护项目。附件 \ufffc\ufffc",
            id="unproven-object-pair-is-not-rewritten",
        ),
        pytest.param(
            "原本没有内联代码。",
            "现在有 \ufffc\ufffcsrc\ufffc\ufffc 内联代码。",
            "现在有 \ufffc\ufffcsrc\ufffc\ufffc 内联代码。",
            id="new-unproven-inline-code-is-not-rewritten",
        ),
    ],
)
def test_obsidian_processor_normalizes_only_proven_inline_code_markers(
    previous, observed, expected
):
    processor = ObsidianHostProcessor()

    assert processor.normalize_tracked_text(previous, observed) == expected


@pytest.mark.parametrize(
    ("committed", "observed", "following", "expected"),
    [
        pytest.param(
            "上一句需要完成复查。",
            "上一句需要完成",
            "筛选。\n\n",
            "上一句需要完成筛选。",
            id="include-short-replacement-after-substantive-tail-deletion",
        ),
        pytest.param(
            "上一句已经结束。",
            "上一句已经结束。下一句开",
            "始。\n",
            "上一句已经结束。",
            id="clip-followup-prefix-already-inside-live-range",
        ),
        pytest.param(
            "上一句已经结束。",
            "上一句已经结束。",
            "下一句开始。\n",
            "上一句已经结束。",
            id="exclude-direct-followup-outside-live-range",
        ),
        pytest.param(
            "上一句已经结束。",
            "上一句已经结束。用户把它明确改写成了两个完整而且独立的句子。",
            "\n",
            "上一句已经结束。用户把它明确改写成了两个完整而且独立的句子。",
            id="preserve-substantive-multi-sentence-rewrite",
        ),
        pytest.param(
            "上一句已经结束。",
            "上一句已经结束",
            "下一句开始。\n",
            "上一句已经结束",
            id="punctuation-deletion-does-not-open-tail-replacement",
        ),
    ],
)
def test_obsidian_processor_repairs_only_a_bounded_tail_replacement(
    committed, observed, following, expected
):
    processor = ObsidianHostProcessor()

    assert (
        processor.normalize_tracked_text(
            committed,
            observed,
            committed_text=committed,
            following_text=following,
        )
        == expected
    )
