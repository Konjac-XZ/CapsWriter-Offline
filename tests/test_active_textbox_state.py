import asyncio
import logging
import time

from src.polish import active_textbox_state
from src.polish.active_textbox_state import ActiveTextBoxState
from src.polish import llm_polish
from src.polish.llm_polish import PolishRequestContext
from src.polish.providers.base import PolishCompletionResult


def test_capture_prefers_uia_state(monkeypatch, caplog):
    uia = ActiveTextBoxState(
        source="uia",
        process_name="Code.exe",
        window_title="project - Visual Studio Code",
        control_type="document",
    )
    fallback_calls = []
    monkeypatch.setattr(active_textbox_state.platform, "system", lambda: "Windows")
    monkeypatch.setattr(active_textbox_state, "_capture_via_uia", lambda: uia)
    monkeypatch.setattr(
        active_textbox_state,
        "_capture_via_win32",
        lambda: fallback_calls.append(True),
    )
    caplog.set_level(
        logging.INFO,
        logger="capswriter.polish.active_textbox_state",
    )

    assert active_textbox_state.get_active_textbox_state() is uia
    assert fallback_calls == []
    assert "captured source=uia process=Code.exe" in caplog.text
    assert "fields=process_name,window_title,control_type" in caplog.text
    assert "project - Visual Studio Code" not in caplog.text


def test_capture_falls_back_to_win32_when_uia_fails(monkeypatch, caplog):
    fallback = ActiveTextBoxState(
        source="win32_focus",
        process_name="notepad.exe",
        window_title="notes.txt - Notepad",
        control_class_name="RichEditD2DPT",
    )
    monkeypatch.setattr(active_textbox_state.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        active_textbox_state,
        "_capture_via_uia",
        lambda: (_ for _ in ()).throw(RuntimeError("UIA unavailable")),
    )
    monkeypatch.setattr(
        active_textbox_state,
        "_capture_via_win32",
        lambda: fallback,
    )
    caplog.set_level(
        logging.INFO,
        logger="capswriter.polish.active_textbox_state",
    )

    assert active_textbox_state.get_active_textbox_state() is fallback
    assert "fallback from=uia to=win32 reason=uia_error:RuntimeError" in caplog.text
    assert "captured source=win32_focus process=notepad.exe" in caplog.text
    assert "notes.txt - Notepad" not in caplog.text


def test_capture_falls_back_when_uia_state_has_no_metadata(monkeypatch):
    fallback = ActiveTextBoxState(
        source="win32_foreground",
        process_name="app.exe",
    )
    monkeypatch.setattr(active_textbox_state.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        active_textbox_state,
        "_capture_via_uia",
        lambda: ActiveTextBoxState(source="uia"),
    )
    monkeypatch.setattr(
        active_textbox_state,
        "_capture_via_win32",
        lambda: fallback,
    )

    assert active_textbox_state.get_active_textbox_state() is fallback


def test_capture_honors_existing_process_exclusions(monkeypatch):
    state = ActiveTextBoxState(source="uia", process_name="WindowsTerminal.exe")
    monkeypatch.setattr(active_textbox_state.platform, "system", lambda: "Windows")
    monkeypatch.setattr(active_textbox_state, "_capture_via_uia", lambda: state)

    assert (
        active_textbox_state.get_active_textbox_state(
            excluded_process_names=("windowsterminal.exe",),
        )
        is None
    )


def test_prefetch_captures_state_independently_of_textbox_content(monkeypatch):
    state = ActiveTextBoxState(
        source="uia",
        process_name="Code.exe",
        window_title="project - Visual Studio Code",
    )
    prepared = object()
    prepare_options = {}
    monkeypatch.setattr(llm_polish, "is_llm_polish_enabled", lambda: True)
    monkeypatch.setattr(llm_polish, "_qwen_asr_context_enabled", lambda: False)
    monkeypatch.setattr(llm_polish, "_qwen_asr_textbox_enabled", lambda: False)
    monkeypatch.setattr(
        llm_polish,
        "_cfg",
        lambda: {
            "textbox_context": {"enabled": False},
            "active_textbox_state": {"enabled": True},
        },
    )
    monkeypatch.setattr(llm_polish, "get_active_textbox_state", lambda **_kwargs: state)

    def prepare(**kwargs):
        prepare_options.update(kwargs)
        return prepared

    monkeypatch.setattr(llm_polish, "_prepare_polish_request_context", prepare)

    assert asyncio.run(llm_polish.prefetch_request_context()) is prepared
    assert prepare_options["captured_textbox_context"] is None
    assert prepare_options["active_textbox_state"] is state
    assert prepare_options["active_textbox_state_prepared"] is True


def test_polish_request_attaches_state_message_and_logs_outcome(monkeypatch, caplog):
    captured_messages = []

    class FakeProvider:
        async def complete(self, request, **_kwargs):
            captured_messages.extend(request.messages)
            return PolishCompletionResult("润色结果", 200)

    async def fake_get_provider(_config):
        return FakeProvider()

    async def no_preference(**_kwargs):
        return None

    state = ActiveTextBoxState(
        source="uia",
        process_name="Code.exe",
        window_title="private project title",
        control_type="document",
        has_keyboard_focus=True,
    )
    context = PolishRequestContext(
        cfg={"enabled": True, "active_textbox_state": {"enabled": True}},
        provider_name="openai_compatible",
        provider_options={"reuse_client": True},
        base_url="https://example.test",
        api_key="test-key",
        model="test-model",
        timeout_s=5,
        temperature=0,
        max_output_tokens=100,
        prompt="校对",
        session_constraint="",
        captured_textbox_context=None,
        textbox_context=None,
        textbox_context_has_position=False,
        active_textbox_state=state,
        vision_context=None,
        history=[],
        asr_history=[],
        lexicon_message=None,
        prepared_at=time.time(),
        timing={},
    )
    monkeypatch.setattr(llm_polish, "_cfg", lambda: context.cfg)
    monkeypatch.setattr(llm_polish, "get_polish_provider", fake_get_provider)
    monkeypatch.setattr(llm_polish, "is_smart_quotes_enabled", lambda: False)
    monkeypatch.setattr(
        "src.personalization.retrieval.retrieve_preference_message",
        no_preference,
    )
    caplog.set_level(logging.INFO, logger="capswriter.polish.llm")

    result = asyncio.run(llm_polish.polish_text("ASR 原文", prepared_context=context))

    assert result == "润色结果"
    state_messages = [
        message["content"]
        for message in captured_messages
        if "当前激活输入控件的环境状态" in message["content"]
    ]
    assert len(state_messages) == 1
    assert "进程名：Code.exe" in state_messages[0]
    assert "窗口标题：private project title" in state_messages[0]
    assert "enabled=True captured=True attached=True source=uia process=Code.exe" in (
        caplog.text
    )
    assert "private project title" not in caplog.text
