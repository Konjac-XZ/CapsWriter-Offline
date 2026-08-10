from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from .protocol import CompositionStyle


@dataclass(frozen=True, slots=True)
class HostApplication:
    process_id: int = 0
    process_name: str | None = None


class RevisionDecision(Enum):
    APPLY = "apply"
    DEFER_FINAL = "defer_final"


class HostProcessor(Protocol):
    name: str
    trust_external_termination_rollback: bool

    def matches(self, host: HostApplication) -> bool: ...

    def process_revision(
        self,
        text: str,
        style: CompositionStyle,
    ) -> RevisionDecision: ...

    def normalize_tracked_text(
        self,
        previous_text: str,
        observed_text: str,
        *,
        committed_text: str | None = None,
        preceding_text: str = "",
        following_text: str = "",
        tail_replacement_open: bool = False,
    ) -> str: ...

    def opens_tail_replacement(
        self, previous_text: str, observed_text: str
    ) -> bool: ...


class DefaultHostProcessor:
    name = "default"
    trust_external_termination_rollback = True

    def matches(self, host: HostApplication) -> bool:
        return True

    def process_revision(
        self,
        text: str,
        style: CompositionStyle,
    ) -> RevisionDecision:
        return RevisionDecision.APPLY

    def normalize_tracked_text(
        self,
        previous_text: str,
        observed_text: str,
        *,
        committed_text: str | None = None,
        preceding_text: str = "",
        following_text: str = "",
        tail_replacement_open: bool = False,
    ) -> str:
        return _trim_followup_after_new_terminal(
            committed_text or previous_text,
            observed_text,
        )

    def opens_tail_replacement(self, previous_text: str, observed_text: str) -> bool:
        return False


class ChatGptHostProcessor(DefaultHostProcessor):
    name = "chatgpt_desktop"
    trust_external_termination_rollback = False

    def matches(self, host: HostApplication) -> bool:
        return (host.process_name or "").casefold() == "chatgpt.exe"

    def process_revision(
        self,
        text: str,
        style: CompositionStyle,
    ) -> RevisionDecision:
        if "\n" in text or "\r" in text:
            return RevisionDecision.DEFER_FINAL
        return RevisionDecision.APPLY


_OBSIDIAN_INLINE_CODE_MARKER = re.compile(r"(?<!\ufffc)\ufffc{2}(?!\ufffc)")
_BACKTICK_RUN = re.compile(r"`+")
_SENTENCE_TERMINATOR = re.compile(r"[。！？!?…]+[\"'”’」』】）》]*")
_NON_SUBSTANTIVE_TAIL = re.compile(r"^[\s。！？!?…\"'“”‘’「」『』【】（）《》]*$")
_MAXIMUM_TRAILING_REPAIR_CHARACTERS = 16


def _trim_followup_after_new_terminal(reference: str, observed: str) -> str:
    """Keep new terminal punctuation without absorbing the next sentence."""
    reference = reference.strip()
    if not reference or observed == reference or not observed.startswith(reference):
        return observed
    suffix = observed[len(reference) :]
    boundary = _SENTENCE_TERMINATOR.match(suffix)
    if boundary is None or boundary.end() == len(suffix):
        return observed
    return observed[: len(reference) + boundary.end()]


class ObsidianHostProcessor(DefaultHostProcessor):
    name = "obsidian"

    def matches(self, host: HostApplication) -> bool:
        return (host.process_name or "").casefold() == "obsidian.exe"

    def normalize_tracked_text(
        self,
        previous_text: str,
        observed_text: str,
        *,
        committed_text: str | None = None,
        preceding_text: str = "",
        following_text: str = "",
        tail_replacement_open: bool = False,
    ) -> str:
        """Restore Obsidian projections without trusting a drifted live range.

        Obsidian Live Preview exposes each single-backtick inline-code delimiter
        as two U+FFFC object replacement characters after the committed text is
        rendered. Only accept that projection when it preserves the complete
        backtick-run signature of the previously tracked logical text. This
        keeps unrelated widgets and fenced-code marker runs untouched.

        The host can also leave an inward-gravity range just before a replacement
        typed at its end, or keep the original range width after a shorter edit.
        Use the committed sentence-boundary count to repair only the adjacent
        terminal run. This includes a short replacement such as ``筛选。`` but
        cuts a following sentence prefix such as ``在这`` after the expected
        terminal boundary.
        """
        candidate = _OBSIDIAN_INLINE_CODE_MARKER.sub("`", observed_text)
        previous_signature = _backtick_run_signature(committed_text or previous_text)
        if candidate != observed_text and (
            not previous_signature
            or _backtick_run_signature(candidate) != previous_signature
        ):
            return observed_text

        reference = (committed_text or previous_text).strip()
        candidate = _repair_obsidian_line_boundary(
            reference,
            candidate,
            preceding_text,
            following_text,
        )
        if reference and not reference.startswith(("\r", "\n")):
            candidate = candidate.lstrip("\r\n")
        return _repair_obsidian_sentence_boundary(
            reference,
            previous_text,
            candidate,
            following_text,
            tail_replacement_open=tail_replacement_open,
        )

    def opens_tail_replacement(self, previous_text: str, observed_text: str) -> bool:
        candidate = _OBSIDIAN_INLINE_CODE_MARKER.sub("`", observed_text)
        return _has_substantive_trailing_deletion(previous_text, candidate)


def _backtick_run_signature(text: str) -> tuple[int, ...]:
    return tuple(len(match.group()) for match in _BACKTICK_RUN.finditer(text))


def _repair_obsidian_line_boundary(
    reference: str,
    observed: str,
    preceding: str,
    following: str,
) -> str:
    """Recover one logical line from a live range that slid on both sides."""
    if not reference or "\n" in reference or "\r" in reference:
        return observed
    observed_lines = observed.splitlines()
    preceding_line = preceding.splitlines()[-1] if preceding else ""
    following_line = following.splitlines()[0] if following else ""
    candidates = [line.strip("\r") for line in observed_lines if line]
    if observed_lines:
        candidates.extend(
            (
                preceding_line + observed_lines[0].strip("\r"),
                observed_lines[-1].strip("\r") + following_line,
            )
        )
    for line in tuple(candidates):
        alignment = fuzz.partial_ratio_alignment(reference, line)
        if alignment is not None and alignment.score >= 60:
            candidates.append(line[alignment.dest_start :])
    if not candidates:
        return observed
    best = max(
        candidates,
        key=lambda line: Levenshtein.normalized_similarity(reference, line),
    )
    best_score = Levenshtein.normalized_similarity(reference, best)
    observed_score = Levenshtein.normalized_similarity(reference, observed.strip())
    if best_score < 0.6 or best_score <= observed_score:
        return observed
    return best


def _repair_obsidian_sentence_boundary(
    reference: str,
    previous: str,
    observed: str,
    following: str,
    *,
    tail_replacement_open: bool,
) -> str:
    expected_boundaries = len(_SENTENCE_TERMINATOR.findall(reference))
    if expected_boundaries == 0:
        return observed

    local_following = following.splitlines(keepends=False)[0] if following else ""
    combined = observed + local_following
    boundaries = list(_SENTENCE_TERMINATOR.finditer(combined))
    if len(boundaries) < expected_boundaries:
        return observed

    repaired_end = boundaries[expected_boundaries - 1].end()
    if (
        repaired_end < len(observed)
        and len(observed) - repaired_end > _MAXIMUM_TRAILING_REPAIR_CHARACTERS
    ):
        return observed
    if repaired_end > len(observed):
        appended_characters = repaired_end - len(observed)
        if appended_characters > _MAXIMUM_TRAILING_REPAIR_CHARACTERS or not (
            tail_replacement_open
            or _has_substantive_trailing_deletion(previous, observed)
        ):
            return observed
    return combined[:repaired_end]


def _has_substantive_trailing_deletion(previous: str, observed: str) -> bool:
    """Return whether the latest edit removed real content at the right edge."""
    opcodes = Levenshtein.opcodes(previous.strip(), observed.strip())
    if not opcodes:
        return False
    tag, source_start, source_end, dest_start, dest_end = opcodes[-1]
    if (
        tag not in {"delete", "replace"}
        or source_end != len(previous.strip())
        or dest_end != len(observed.strip())
    ):
        return False
    removed = previous.strip()[source_start:source_end]
    inserted = observed.strip()[dest_start:dest_end]
    return bool(removed) and (
        _NON_SUBSTANTIVE_TAIL.fullmatch(removed) is None
        or _NON_SUBSTANTIVE_TAIL.fullmatch(inserted) is None
    )


class HostProcessorRegistry:
    def __init__(
        self,
        processors: tuple[HostProcessor, ...] | None = None,
        default: HostProcessor | None = None,
    ) -> None:
        self._processors = (
            processors
            if processors is not None
            else (ChatGptHostProcessor(), ObsidianHostProcessor())
        )
        self._default = default or DefaultHostProcessor()

    def resolve(self, host: HostApplication) -> HostProcessor:
        for processor in self._processors:
            if processor.matches(host):
                return processor
        return self._default


DEFAULT_HOST_PROCESSOR_REGISTRY = HostProcessorRegistry()
