import asyncio

from src.polish.context_providers import (
    ContextCaptureOptions,
    ContextProvider,
    ContextProviderRegistry,
    TsfContextProvider,
)
from src.polish.textbox_context import TextBoxContext
from src.tsf_ipc.context_snapshot import TsfContextSnapshot


class StubProvider(ContextProvider):
    def __init__(
        self,
        name: str,
        result: TextBoxContext | Exception | None,
    ) -> None:
        self.name = name
        self.result = result
        self.calls = 0

    async def capture(
        self,
        options: ContextCaptureOptions,
    ) -> TextBoxContext | None:
        del options
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_registry_prefers_tsf_context_without_calling_uia():
    tsf_context = TextBoxContext(text="TSF 上下文", source="tsf")
    tsf = StubProvider("tsf", tsf_context)
    uia = StubProvider("uia", TextBoxContext(text="UIA 上下文", source="uia"))
    registry = ContextProviderRegistry((tsf, uia))

    captured = asyncio.run(registry.capture(ContextCaptureOptions()))

    assert captured is tsf_context
    assert tsf.calls == 1
    assert uia.calls == 0


def test_registry_treats_empty_tsf_snapshot_as_authoritative():
    tsf_context = TextBoxContext(text="", source="tsf")
    tsf = StubProvider("tsf", tsf_context)
    uia = StubProvider("uia", TextBoxContext(text="UIA 上下文", source="uia"))
    registry = ContextProviderRegistry((tsf, uia))

    captured = asyncio.run(registry.capture(ContextCaptureOptions()))

    assert captured is tsf_context
    assert uia.calls == 0


def test_registry_falls_back_to_uia_when_tsf_is_unavailable():
    uia_context = TextBoxContext(text="UIA 上下文", source="uia")
    tsf = StubProvider("tsf", None)
    uia = StubProvider("uia", uia_context)
    registry = ContextProviderRegistry((tsf, uia))

    captured = asyncio.run(registry.capture(ContextCaptureOptions()))

    assert captured is uia_context
    assert tsf.calls == 1
    assert uia.calls == 1


def test_registry_continues_after_provider_error():
    uia_context = TextBoxContext(text="UIA 上下文", source="uia")
    registry = ContextProviderRegistry(
        (
            StubProvider("tsf", RuntimeError("unavailable")),
            StubProvider("uia", uia_context),
        )
    )

    assert asyncio.run(registry.capture(ContextCaptureOptions())) is uia_context


def test_tsf_provider_maps_host_and_selection_metadata(monkeypatch):
    snapshot = TsfContextSnapshot(
        text="前文选中后文",
        caret_offset=4,
        selection_start=2,
        selection_end=4,
        process_id=321,
        process_name="Editor.exe",
    )

    class Bridge:
        async def query_context(self):
            return snapshot

    monkeypatch.setattr(
        "src.polish.context_providers.get_tsf_speech_tip_bridge",
        lambda: Bridge(),
    )

    captured = asyncio.run(TsfContextProvider().capture(ContextCaptureOptions()))

    assert captured is not None
    assert captured.source == "tsf"
    assert captured.caret_source == "tsf_selection"
    assert captured.process_id == 321
    assert captured.process_name == "Editor.exe"
    assert captured.selection_start == 2
    assert captured.selection_end == 4


def test_tsf_provider_honors_process_exclusion(monkeypatch):
    snapshot = TsfContextSnapshot(
        text="private console text",
        caret_offset=0,
        selection_start=0,
        selection_end=0,
        process_id=321,
        process_name="WindowsTerminal.exe",
    )

    class Bridge:
        async def query_context(self):
            return snapshot

    monkeypatch.setattr(
        "src.polish.context_providers.get_tsf_speech_tip_bridge",
        lambda: Bridge(),
    )

    captured = asyncio.run(
        TsfContextProvider().capture(
            ContextCaptureOptions(
                excluded_process_names=("windowsterminal.exe",),
            )
        )
    )

    assert captured is None
