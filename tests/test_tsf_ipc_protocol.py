import uuid

import pytest

from src.tsf_ipc.protocol import (
    HEADER,
    MAGIC,
    Frame,
    Operation,
    Status,
    decode_frame,
    decode_header,
    encode_frame,
)


def test_tsf_frame_round_trip_preserves_utf16_and_uuid_byte_order():
    frame = Frame(
        operation=Operation.REVISE,
        session_id=uuid.UUID("00112233-4455-6677-8899-aabbccddeeff"),
        revision=42,
        text="语音 revision 😀",
        status=Status.APPLIED,
    )

    encoded = encode_frame(frame)
    decoded = decode_frame(encoded[: HEADER.size], encoded[HEADER.size :])

    assert encoded[:4] == MAGIC.to_bytes(4, "little")
    assert decoded == frame


@pytest.mark.parametrize(
    "mutator, message",
    [
        (lambda header: b"NOPE" + header[4:], "magic"),
        (
            lambda header: header[:4] + (99).to_bytes(2, "little") + header[6:],
            "version",
        ),
    ],
)
def test_tsf_frame_rejects_incompatible_headers(mutator, message):
    frame = Frame(Operation.BEGIN, uuid.uuid4(), 1, "hello")
    encoded = encode_frame(frame)

    with pytest.raises(ValueError, match=message):
        decode_header(mutator(encoded[: HEADER.size]))


def test_tsf_ack_exposes_original_operation():
    frame = Frame(
        int(Operation.ACK_FLAG) | int(Operation.BEGIN),
        uuid.uuid4(),
        1,
        status=Status.IGNORED_NOT_FOREGROUND,
    )

    assert frame.is_ack is True
    assert frame.acknowledged_operation == Operation.BEGIN
