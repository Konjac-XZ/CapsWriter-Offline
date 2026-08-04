import pytest

from src.tsf_ipc.host_processor import (
    ChatGptHostProcessor,
    DefaultHostProcessor,
    HostApplication,
    HostProcessorRegistry,
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
