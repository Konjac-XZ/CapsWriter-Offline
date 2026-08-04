from dataclasses import dataclass


CONTEXT_SNAPSHOT_PREFIX = "CWCTX1\n"
_ACTIVE_END_START = 1
_ACTIVE_END_END = 2


@dataclass(frozen=True, slots=True)
class TsfContextSnapshot:
    text: str
    caret_offset: int
    selection_start: int
    selection_end: int
    process_id: int = 0
    process_name: str | None = None


def encode_context_snapshot(
    prefix: str,
    selection: str,
    suffix: str,
    *,
    active_end: int = _ACTIVE_END_END,
) -> str:
    return (
        f"{CONTEXT_SNAPSHOT_PREFIX}"
        f"{_utf16_units(prefix)}\n"
        f"{_utf16_units(selection)}\n"
        f"{active_end}\n"
        f"{prefix}{selection}{suffix}"
    )


def decode_context_snapshot(
    payload: str,
    *,
    process_id: int = 0,
    process_name: str | None = None,
) -> TsfContextSnapshot:
    if not payload.startswith(CONTEXT_SNAPSHOT_PREFIX):
        raise ValueError("invalid TSF context snapshot prefix")
    fields = payload[len(CONTEXT_SNAPSHOT_PREFIX) :].split("\n", 3)
    if len(fields) != 4:
        raise ValueError("incomplete TSF context snapshot")
    try:
        prefix_units = int(fields[0])
        selection_units = int(fields[1])
        active_end = int(fields[2])
    except ValueError as exc:
        raise ValueError("invalid TSF context snapshot metadata") from exc
    if prefix_units < 0 or selection_units < 0:
        raise ValueError("negative TSF context snapshot length")

    text = fields[3]
    selection_start = _index_at_utf16_units(text, prefix_units)
    selection_end = _index_at_utf16_units(text, prefix_units + selection_units)
    caret_offset = selection_start if active_end == _ACTIVE_END_START else selection_end
    return TsfContextSnapshot(
        text=text,
        caret_offset=caret_offset,
        selection_start=selection_start,
        selection_end=selection_end,
        process_id=process_id,
        process_name=process_name,
    )


def _utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def _index_at_utf16_units(text: str, units: int) -> int:
    raw = text.encode("utf-16-le")
    boundary = units * 2
    if boundary > len(raw):
        raise ValueError("TSF context snapshot length exceeds payload")
    try:
        return len(raw[:boundary].decode("utf-16-le"))
    except UnicodeDecodeError as exc:
        raise ValueError("TSF context snapshot splits a UTF-16 character") from exc
