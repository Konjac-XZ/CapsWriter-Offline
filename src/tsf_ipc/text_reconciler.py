from __future__ import annotations

from dataclasses import dataclass

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from .context_snapshot import TsfContextSnapshot


ANCHOR_SIZE = 96
MINIMUM_CONFIDENCE = 0.72
INCREMENTAL_MINIMUM_CONFIDENCE = 0.55
MINIMUM_TEXT_CONTINUITY = 0.5
MINIMUM_LENGTH_CONTINUITY = 0.4


@dataclass(frozen=True, slots=True)
class ReconciledText:
    text: str
    alignment_score: float
    start: int
    end: int


@dataclass(slots=True)
class IncrementalTextTracker:
    """Advance a tracked interval through consecutive stable snapshots."""

    snapshot: TsfContextSnapshot

    def advance(self, current: TsfContextSnapshot) -> ReconciledText | None:
        result = reconcile_tracked_text(
            self.snapshot,
            current,
            minimum_confidence=INCREMENTAL_MINIMUM_CONFIDENCE,
        )
        if result is None:
            return None
        self.snapshot = TsfContextSnapshot(
            text=current.text,
            caret_offset=result.end,
            selection_start=result.start,
            selection_end=result.end,
            process_id=current.process_id,
            process_name=current.process_name,
        )
        return result

    def recover(self, current: TsfContextSnapshot, result: ReconciledText) -> None:
        """Restart the incremental chain after baseline-based recovery."""
        self.snapshot = TsfContextSnapshot(
            text=current.text,
            caret_offset=result.end,
            selection_start=result.start,
            selection_end=result.end,
            process_id=current.process_id,
            process_name=current.process_name,
        )


def reconcile_tracked_text(
    baseline: TsfContextSnapshot,
    current: TsfContextSnapshot,
    *,
    minimum_confidence: float = MINIMUM_CONFIDENCE,
) -> ReconciledText | None:
    """Map the baseline selection through a whole-window text alignment."""
    if baseline.selection_end < baseline.selection_start:
        return None
    opcodes = Levenshtein.opcodes(baseline.text, current.text)
    start = _map_boundary(opcodes, baseline.selection_start, include_insert=False)
    end = _map_boundary(opcodes, baseline.selection_end, include_insert=True)
    if start is None or end is None or not 0 <= start <= end <= len(current.text):
        return None

    mapped_interval = (start, end)
    candidates = [mapped_interval]
    native_interval = (current.selection_start, current.selection_end)
    if native_interval != candidates[0] and 0 <= native_interval[0] <= native_interval[
        1
    ] <= len(current.text):
        candidates.append(native_interval)

    left = baseline.text[
        max(0, baseline.selection_start - ANCHOR_SIZE) : baseline.selection_start
    ]
    right = baseline.text[baseline.selection_end : baseline.selection_end + ANCHOR_SIZE]
    original = baseline.text[baseline.selection_start : baseline.selection_end]
    scored: list[tuple[float, int, int]] = []
    for item_start, item_end in candidates:
        score = _interval_confidence(
            original, left, right, current.text, item_start, item_end
        )
        if score is not None:
            scored.append((score, item_start, item_end))
    if not scored:
        return None
    confidence, start, end = _choose_interval(
        scored,
        mapped_interval=mapped_interval,
        native_interval=native_interval,
        current_text=current.text,
        minimum_confidence=minimum_confidence,
    )
    accepted_confidence = (
        min(minimum_confidence, 0.4)
        if (start, end) == native_interval
        else minimum_confidence
    )
    if confidence < accepted_confidence:
        return None
    candidate = current.text[start:end]
    return ReconciledText(candidate, confidence, start, end)


def has_meaningful_tracking_anchor(snapshot: TsfContextSnapshot) -> bool:
    """Return whether the tracked span has non-whitespace surrounding text."""
    left = snapshot.text[: snapshot.selection_start]
    right = snapshot.text[snapshot.selection_end :]
    return bool(left.strip() or right.strip())


def is_plausible_tracking_edit(previous: str, candidate: str) -> bool:
    """Reject whole-control replacement while retaining ordinary local edits."""
    before = previous.strip()
    after = candidate.strip()
    if not before or not after:
        return False
    if before == after:
        return True
    longest = max(len(before), len(after))
    shortest = min(len(before), len(after))
    if shortest / longest < MINIMUM_LENGTH_CONTINUITY:
        return False
    return fuzz.ratio(before, after) / 100.0 >= MINIMUM_TEXT_CONTINUITY


def _choose_interval(
    scored: list[tuple[float, int, int]],
    *,
    mapped_interval: tuple[int, int],
    native_interval: tuple[int, int],
    current_text: str,
    minimum_confidence: float,
) -> tuple[float, int, int]:
    by_interval = {(start, end): score for score, start, end in scored}
    native_score = by_interval.get(native_interval)
    mapped_score = by_interval.get(mapped_interval)
    # The live TSF range is strong positional evidence even when the user has
    # rewritten both the target and one adjacent anchor in a single edit.
    native_minimum = min(minimum_confidence, 0.4)
    if native_score is None or native_score < native_minimum:
        return max(scored)
    if mapped_score is None:
        return native_score, *native_interval

    mapped_text = current_text[slice(*mapped_interval)]
    native_text = current_text[slice(*native_interval)]
    boundary_repair = abs(len(mapped_text) - len(native_text)) <= 2 and (
        mapped_text.endswith(native_text) or mapped_text.startswith(native_text)
    )
    if boundary_repair and mapped_score > native_score:
        return mapped_score, *mapped_interval
    return native_score, *native_interval


def _interval_confidence(
    original: str,
    left: str,
    right: str,
    current_text: str,
    start: int,
    end: int,
) -> float | None:
    candidate = current_text[start:end]
    current_left = current_text[max(0, start - len(left)) : start]
    current_right = current_text[end : end + len(right)]
    weighted_scores: list[tuple[float, float]] = []
    if left.strip():
        weighted_scores.append((fuzz.ratio(left, current_left) / 100.0, 0.4))
    if right.strip():
        weighted_scores.append((fuzz.ratio(right, current_right) / 100.0, 0.4))
    if original or candidate:
        weighted_scores.append((fuzz.ratio(original, candidate) / 100.0, 0.2))
    if not weighted_scores:
        return None
    return sum(score * weight for score, weight in weighted_scores) / sum(
        weight for _, weight in weighted_scores
    )


def _map_boundary(opcodes, position: int, *, include_insert: bool) -> int | None:
    insertion_at_boundary: tuple[int, int] | None = None
    for opcode in opcodes:
        tag, source_start, source_end, dest_start, dest_end = opcode
        if tag == "insert" and source_start == position:
            insertion_at_boundary = (dest_start, dest_end)
            continue
        if source_start <= position <= source_end:
            if tag == "equal":
                mapped = dest_start + min(
                    position - source_start, dest_end - dest_start
                )
            elif position >= source_end:
                mapped = dest_end
            else:
                mapped = dest_start
            if insertion_at_boundary is not None:
                return (
                    insertion_at_boundary[1]
                    if include_insert
                    else insertion_at_boundary[0]
                )
            return mapped
    if insertion_at_boundary is not None:
        return insertion_at_boundary[1] if include_insert else insertion_at_boundary[0]
    return None
