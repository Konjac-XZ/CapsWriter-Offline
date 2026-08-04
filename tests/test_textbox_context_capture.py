from unittest.mock import patch

from src.polish.textbox_context import (
    _UiaTextResult,
    get_active_textbox_context,
    has_meaningful_textbox_text,
)


def _capture_patches(*, clipboard_text: str | None = None):
    return (
        patch("src.polish.textbox_context.platform.system", return_value="Windows"),
        patch("src.polish.textbox_context._get_focused_hwnd", return_value=123),
        patch("src.polish.textbox_context._get_class_name", return_value="Edit"),
        patch("src.polish.textbox_context._get_window_process_id", return_value=456),
        patch("src.polish.textbox_context._safe_process_name", return_value="app.exe"),
        patch(
            "src.polish.textbox_context._read_text_via_uia",
            return_value=(None, 123, "Edit", False),
        ),
        patch(
            "src.polish.textbox_context._read_text_via_clipboard_copy",
            return_value=clipboard_text,
        ),
    )


def test_clipboard_fallback_is_disabled_by_default():
    patches = _capture_patches(clipboard_text="clipboard text")
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6] as clipboard_copy,
    ):
        context = get_active_textbox_context()

    assert context is None
    clipboard_copy.assert_not_called()


def test_clipboard_fallback_can_be_enabled_explicitly():
    patches = _capture_patches(clipboard_text="clipboard text")
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patches[5],
        patches[6] as clipboard_copy,
    ):
        context = get_active_textbox_context(clipboard_fallback_enabled=True)

    assert context is not None
    assert context.text == "clipboard text"
    assert context.source == "clipboard"
    clipboard_copy.assert_called_once_with(debug=False)


def test_invisible_only_text_is_not_meaningful():
    for text in (None, "", " \t\r\n", "\u200b", "\ufeff", "\x00\u200d\ufe0f"):
        assert not has_meaningful_textbox_text(text)


def test_visible_text_remains_meaningful():
    for text in ("上下文", "...", "👩\u200d💻"):
        assert has_meaningful_textbox_text(text)


def test_uia_does_not_return_invisible_only_context():
    patches = _capture_patches()
    with (
        patches[0],
        patches[1],
        patches[2],
        patches[3],
        patches[4],
        patch(
            "src.polish.textbox_context._read_text_via_uia",
            return_value=(
                _UiaTextResult(text="\u200b", source="uia_text"),
                123,
                "Edit",
                False,
            ),
        ),
    ):
        context = get_active_textbox_context()

    assert context is None
