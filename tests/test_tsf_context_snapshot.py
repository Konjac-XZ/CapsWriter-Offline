import pytest

from src.tsf_ipc.context_snapshot import (
    decode_context_snapshot,
    encode_context_snapshot,
)


def test_context_snapshot_round_trip_preserves_caret_selection_and_unicode():
    payload = encode_context_snapshot(
        "前文😀",
        "选中\n文本",
        "后文",
        active_end=1,
    )

    snapshot = decode_context_snapshot(
        payload,
        process_id=123,
        process_name="Editor.exe",
    )

    assert snapshot.text == "前文😀选中\n文本后文"
    assert snapshot.selection_start == len("前文😀")
    assert snapshot.selection_end == len("前文😀选中\n文本")
    assert snapshot.caret_offset == snapshot.selection_start
    assert snapshot.process_id == 123
    assert snapshot.process_name == "Editor.exe"


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "CWCTX1\nmissing",
        "CWCTX1\n-1\n0\n0\ntext",
        "CWCTX1\n20\n0\n0\nshort",
        "CWCTX1\n1\n0\n0\n😀",
    ],
)
def test_context_snapshot_rejects_invalid_payload(payload):
    with pytest.raises(ValueError):
        decode_context_snapshot(payload)
