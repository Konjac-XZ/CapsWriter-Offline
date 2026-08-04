from __future__ import annotations

import struct
import uuid
from dataclasses import dataclass
from enum import IntEnum


MAGIC = 0x50545743  # "CWTP" as little-endian bytes
VERSION = 1
HEADER = struct.Struct("<IHHQ16sII")
MAX_TEXT_BYTES = 4 * 1024 * 1024


class Operation(IntEnum):
    HELLO = 0
    BEGIN = 1
    REVISE = 2
    COMMIT = 3
    CANCEL = 4
    PING = 5
    COMPOSITION_TERMINATED = 6
    ACK_FLAG = 0x8000


class Status(IntEnum):
    APPLIED = 0
    IGNORED_NOT_FOREGROUND = 1
    STALE_REVISION = 2
    NO_CONTEXT = 3
    EDIT_SESSION_FAILED = 4
    INACTIVE_SESSION = 5
    QUEUED = 6
    INVALID_FRAME = 7


class CompositionStyle(IntEnum):
    """Visual state carried in the request header's status field.

    ACK frames continue to use that field for ``Status``. Keeping the wire
    layout and protocol version unchanged lets already-loaded older TIPs ignore
    the new request metadata during a side-by-side DLL update.
    """

    TRANSCRIPTION = 0
    POLISHING = 1


@dataclass(frozen=True, slots=True)
class Frame:
    operation: int
    session_id: uuid.UUID
    revision: int = 0
    text: str = ""
    status: int = 0

    @property
    def is_ack(self) -> bool:
        return bool(self.operation & int(Operation.ACK_FLAG))

    @property
    def acknowledged_operation(self) -> int:
        return self.operation & ~int(Operation.ACK_FLAG)


def encode_frame(frame: Frame) -> bytes:
    text_bytes = frame.text.encode("utf-16-le")
    if len(text_bytes) > MAX_TEXT_BYTES:
        raise ValueError("TSF IPC text payload is too large")
    return (
        HEADER.pack(
            MAGIC,
            VERSION,
            int(frame.operation),
            int(frame.revision),
            frame.session_id.bytes_le,
            len(text_bytes),
            int(frame.status),
        )
        + text_bytes
    )


def decode_header(data: bytes) -> tuple[int, uuid.UUID, int, int, int]:
    if len(data) != HEADER.size:
        raise ValueError(f"expected {HEADER.size} header bytes, got {len(data)}")
    magic, version, operation, revision, session_bytes, text_size, status = (
        HEADER.unpack(data)
    )
    if magic != MAGIC:
        raise ValueError("invalid TSF IPC magic")
    if version != VERSION:
        raise ValueError(f"unsupported TSF IPC version: {version}")
    if text_size > MAX_TEXT_BYTES or text_size % 2:
        raise ValueError("invalid TSF IPC UTF-16 payload size")
    return operation, uuid.UUID(bytes_le=session_bytes), revision, text_size, status


def decode_frame(header: bytes, payload: bytes) -> Frame:
    operation, session_id, revision, text_size, status = decode_header(header)
    if len(payload) != text_size:
        raise ValueError(f"expected {text_size} payload bytes, got {len(payload)}")
    return Frame(
        operation=operation,
        session_id=session_id,
        revision=revision,
        text=payload.decode("utf-16-le"),
        status=status,
    )
