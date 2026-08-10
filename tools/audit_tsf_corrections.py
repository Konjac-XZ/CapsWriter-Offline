"""Audit persisted TSF correction captures against runtime tracking logs.

This is a read-only diagnostic tool.  It never changes CapsWriter state.  The
parser intentionally uses conservative, explainable rules: ``reject`` means a
capture has evidence that it includes unrelated document text or has lost most
of the committed speech span; ``review`` means the evidence is concerning but
not conclusive; and ``pass`` only means that these heuristics found no obvious
problem.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Iterable, Sequence


APP_DIRECTORY_NAME = "CapsWriter-Offline"
LOG_LINE = re.compile(
    r"^(?P<timestamp>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) "
    r"(?P<level>\S+)\s+\d+ capswriter\.tsf\.bridge: (?P<message>.*)$"
)
HOST_SELECTED = re.compile(
    r"TSF host selected session=(?P<session>[0-9a-f]{8}) .*?process=(?P<process>\S+)"
)
SNAPSHOT = re.compile(
    r"TSF tracking snapshot session=(?P<session>[0-9a-f]{8}) "
    r"revision=(?P<revision>\d+) kind=(?P<kind>baseline|current) "
    r"target_start=(?P<start>\d+) target_end=(?P<end>\d+) text=(?P<text>.+)$"
)
RECONCILIATION = re.compile(
    r"TSF tracking reconciliation session=(?P<session>[0-9a-f]{8}) "
    r"mode=(?P<mode>\S+) alignment_score=(?P<score>\d+(?:\.\d+)?) "
    r"history_matched=(?P<matched>True|False) text=(?P<text>.+)$"
)
TRACKING_STOPPED = re.compile(
    r"TSF tracking stopped session=(?P<session>[0-9a-f]{8}) reason=(?P<reason>\S+)"
)


@dataclass(slots=True)
class LogEvidence:
    process: str | None = None
    baseline_start: int | None = None
    baseline_end: int | None = None
    baseline_chars: int | None = None
    current_start: int | None = None
    current_end: int | None = None
    current_chars: int | None = None
    prefix_window_full: bool = False
    suffix_window_full: bool = False
    alignment_score: float | None = None
    reconciliation_mode: str | None = None
    reconciliation_text: str | None = None
    stopped_reason: str | None = None
    last_log_time: str | None = None


@dataclass(slots=True)
class CorrectionCapture:
    id: int
    session_id: str
    asr_text: str
    committed_text: str
    corrected_text: str
    event_revision: int
    processed_revision: int
    first_observed_at: float
    last_observed_at: float
    preference_evidence_count: int = 0


@dataclass(slots=True)
class AuditResult:
    capture: CorrectionCapture
    verdict: str
    reasons: list[str]
    similarity: float
    length_ratio: float
    longest_match_ratio: float
    evidence: LogEvidence = field(default_factory=LogEvidence)

    def to_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["observed_at"] = (
            datetime.fromtimestamp(self.capture.last_observed_at)
            .astimezone()
            .isoformat(timespec="seconds")
        )
        return payload


def default_app_directory() -> Path:
    if local_app_data := os.environ.get("LOCALAPPDATA"):
        return Path(local_app_data) / APP_DIRECTORY_NAME
    if app_data := os.environ.get("APPDATA"):
        return Path(app_data) / APP_DIRECTORY_NAME
    return Path.home() / "AppData" / "Local" / APP_DIRECTORY_NAME


def parse_timestamp(value: str | None) -> float | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.astimezone()
    return parsed.timestamp()


def read_captures(database_path: Path, since: float | None) -> list[CorrectionCapture]:
    uri = f"{database_path.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=3.0) as database:
        database.row_factory = sqlite3.Row
        query = (
            "SELECT correction_events.id, correction_events.session_id, "
            "correction_events.asr_text, correction_events.committed_text, "
            "correction_events.corrected_text, correction_events.event_revision, "
            "correction_events.processed_revision, "
            "correction_events.first_observed_at, correction_events.last_observed_at, "
            "COUNT(preference_evidence.preference_id) AS "
            "preference_evidence_count FROM correction_events LEFT JOIN "
            "preference_evidence ON preference_evidence.correction_event_id = "
            "correction_events.id"
        )
        parameters: tuple[float, ...] = ()
        if since is not None:
            query += " WHERE last_observed_at >= ?"
            parameters = (since,)
        query += " GROUP BY correction_events.id ORDER BY last_observed_at, correction_events.id"
        rows = database.execute(query, parameters).fetchall()
    return [CorrectionCapture(**dict(row)) for row in rows]


def _literal_text(value: str) -> str | None:
    try:
        parsed = ast.literal_eval(value)
    except (SyntaxError, ValueError):
        return None
    return parsed if isinstance(parsed, str) else None


def _iter_log_lines(paths: Iterable[Path]) -> Iterable[tuple[datetime, str]]:
    parsed: list[tuple[datetime, str]] = []
    for path in paths:
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            match = LOG_LINE.match(line)
            if match is None:
                continue
            parsed.append(
                (
                    datetime.strptime(match.group("timestamp"), "%Y-%m-%d %H:%M:%S"),
                    match.group("message"),
                )
            )
    parsed.sort(key=lambda item: item[0])
    return parsed


def parse_log_evidence(
    paths: Iterable[Path], since: float | None = None
) -> dict[str, LogEvidence]:
    working_by_session: dict[str, LogEvidence] = {}
    reconciled_by_session: dict[str, LogEvidence] = {}
    for timestamp, message in _iter_log_lines(paths):
        local_timestamp = timestamp.astimezone().timestamp()
        if since is not None and local_timestamp < since:
            continue
        if match := HOST_SELECTED.search(message):
            evidence = working_by_session.setdefault(
                match.group("session"), LogEvidence()
            )
            evidence.process = match.group("process")
            evidence.last_log_time = timestamp.isoformat(sep=" ")
            continue
        if match := SNAPSHOT.search(message):
            evidence = working_by_session.setdefault(
                match.group("session"), LogEvidence()
            )
            text = _literal_text(match.group("text"))
            start = int(match.group("start"))
            end = int(match.group("end"))
            if match.group("kind") == "baseline":
                evidence.baseline_start = start
                evidence.baseline_end = end
                evidence.baseline_chars = len(text) if text is not None else None
            else:
                evidence.current_start = start
                evidence.current_end = end
                evidence.current_chars = len(text) if text is not None else None
                evidence.prefix_window_full = start >= 1024
                evidence.suffix_window_full = (
                    text is not None and len(text) - end >= 1024
                )
            evidence.last_log_time = timestamp.isoformat(sep=" ")
            continue
        if match := RECONCILIATION.search(message):
            session = match.group("session")
            evidence = working_by_session.setdefault(session, LogEvidence())
            evidence.alignment_score = float(match.group("score"))
            evidence.reconciliation_mode = match.group("mode")
            evidence.reconciliation_text = _literal_text(match.group("text"))
            evidence.last_log_time = timestamp.isoformat(sep=" ")
            # Freeze the snapshot that immediately preceded reconciliation.
            # Later snapshots from the same session must not rewrite the range
            # evidence for the correction that was actually persisted.
            reconciled_by_session[session] = replace(evidence)
            continue
        if match := TRACKING_STOPPED.search(message):
            session = match.group("session")
            evidence = working_by_session.setdefault(session, LogEvidence())
            evidence.stopped_reason = match.group("reason")
            evidence.last_log_time = timestamp.isoformat(sep=" ")
            if session in reconciled_by_session:
                reconciled_by_session[session].stopped_reason = match.group("reason")
    return working_by_session | reconciled_by_session


def _normalized(value: str) -> str:
    return " ".join(value.split())


def _introduced_document_boundary(original: str, corrected: str) -> bool:
    """Detect a single-line utterance captured together with adjacent document text."""
    if "\n" in original or "\r" in original:
        return False
    lines = [line.strip() for line in corrected.splitlines() if line.strip()]
    return len(lines) >= 2


def classify_capture(
    capture: CorrectionCapture, evidence: LogEvidence | None = None
) -> AuditResult:
    evidence = evidence or LogEvidence()
    committed = _normalized(capture.committed_text)
    corrected = _normalized(capture.corrected_text)
    matcher = SequenceMatcher(None, committed, corrected, autojunk=False)
    similarity = matcher.ratio() if committed or corrected else 1.0
    length_ratio = len(corrected) / max(1, len(committed))
    matching_blocks = [block for block in matcher.get_matching_blocks() if block.size]
    longest_match = max((block.size for block in matching_blocks), default=0)
    longest_match_ratio = longest_match / max(1, len(committed))

    reject_reasons: list[str] = []
    review_reasons: list[str] = []

    if committed == corrected:
        reject_reasons.append("correction_events 保留了已恢复原文的无效修改记录")

    if length_ratio < 0.4:
        reject_reasons.append("修改后文本不足原提交长度的 40%")
    elif length_ratio > 2.5:
        reject_reasons.append("修改后文本超过原提交长度的 2.5 倍")

    if similarity < 0.35:
        reject_reasons.append("原提交与捕捉文本的序列相似度低于 0.35")
    elif similarity < 0.55:
        review_reasons.append("原提交与捕捉文本的序列相似度低于 0.55")

    if longest_match_ratio < 0.35 and similarity < 0.55:
        reject_reasons.append("最长共同片段不足原提交长度的 35%")
    elif longest_match_ratio < 0.6 and similarity < 0.7:
        review_reasons.append("最长共同片段不足原提交长度的 60%")

    if _introduced_document_boundary(capture.committed_text, capture.corrected_text):
        reject_reasons.append("单行语音被捕捉成多行文本，疑似夹入相邻文档内容")

    appended = corrected[len(committed) :] if corrected.startswith(committed) else ""
    if len(appended.strip()) >= 8:
        review_reasons.append("捕捉范围在原提交后继续吸收了另一段完整文本")

    if matching_blocks:
        first_match = matching_blocks[0]
        if (
            0 < first_match.a <= 3
            and first_match.b == 0
            and committed[: first_match.a].isalnum()
        ):
            review_reasons.append("捕捉文本从词内开始，疑似丢失 live range 开头")

    clipped_window = evidence.prefix_window_full or evidence.suffix_window_full
    if clipped_window and similarity < 0.65:
        reject_reasons.append("低相似修改来自触及 1024 字符截断边界的快照")
    elif clipped_window:
        review_reasons.append("快照触及 1024 字符上下文截断边界")

    if (
        evidence.alignment_score is not None
        and evidence.alignment_score >= 0.9
        and similarity < 0.55
    ):
        reject_reasons.append("日志对齐高分与实际文本低相似度矛盾")

    if reject_reasons:
        verdict = "reject"
        reasons = reject_reasons + review_reasons
    elif review_reasons:
        verdict = "review"
        reasons = review_reasons
    else:
        verdict = "pass"
        reasons = ["未命中保守的明显异常规则"]

    return AuditResult(
        capture=capture,
        verdict=verdict,
        reasons=reasons,
        similarity=similarity,
        length_ratio=length_ratio,
        longest_match_ratio=longest_match_ratio,
        evidence=evidence,
    )


def audit(
    captures: Sequence[CorrectionCapture], evidence_by_session: dict[str, LogEvidence]
) -> list[AuditResult]:
    return [
        classify_capture(capture, evidence_by_session.get(capture.session_id[:8]))
        for capture in captures
    ]


def _preview(value: str, limit: int) -> str:
    rendered = value.replace("\r", "\\r").replace("\n", "\\n")
    if len(rendered) <= limit:
        return rendered
    return rendered[: limit - 1] + "…"


def print_human(
    results: Sequence[AuditResult], *, preview_chars: int, summary_only: bool = False
) -> None:
    counts = {
        verdict: sum(result.verdict == verdict for result in results)
        for verdict in ("reject", "review", "pass")
    }
    print(
        "TSF correction audit: "
        f"total={len(results)} reject={counts['reject']} "
        f"review={counts['review']} pass={counts['pass']} "
        "preference_links="
        f"{sum(result.capture.preference_evidence_count for result in results)}"
    )
    if summary_only:
        return
    for result in results:
        capture = result.capture
        evidence = result.evidence
        observed = datetime.fromtimestamp(capture.last_observed_at).astimezone()
        print()
        print(
            f"[{result.verdict.upper()}] {observed.isoformat(timespec='seconds')} "
            f"session={capture.session_id[:8]} process={evidence.process or '-'} "
            f"similarity={result.similarity:.3f} length_ratio={result.length_ratio:.3f} "
            f"longest_match={result.longest_match_ratio:.3f} "
            f"preference_links={capture.preference_evidence_count}"
        )
        if evidence.alignment_score is not None:
            print(
                f"  log: alignment={evidence.alignment_score:.3f} "
                f"mode={evidence.reconciliation_mode or '-'} "
                f"current_range={evidence.current_start}:{evidence.current_end} "
                f"snapshot_chars={evidence.current_chars}"
            )
        for reason in result.reasons:
            print(f"  reason: {reason}")
        print(f"  committed: {_preview(capture.committed_text, preview_chars)}")
        print(f"  captured:  {_preview(capture.corrected_text, preview_chars)}")


def build_argument_parser() -> argparse.ArgumentParser:
    app_directory = default_app_directory()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=app_directory / "State" / "capswriter.db",
        help="CapsWriter SQLite state database (opened read-only)",
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        default=app_directory / "Logs",
        help="directory containing capswriter.log and rotated backups",
    )
    parser.add_argument(
        "--since",
        help="local ISO timestamp, for example 2026-08-10T12:00:00",
    )
    parser.add_argument(
        "--verdict",
        choices=("reject", "review", "pass", "all"),
        default="all",
        help="only print one verdict (default: all)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON lines")
    parser.add_argument(
        "--summary-only", action="store_true", help="omit individual capture details"
    )
    parser.add_argument("--preview-chars", type=int, default=180)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_argument_parser().parse_args(argv)
    since = parse_timestamp(args.since)
    if not args.db.is_file():
        print(f"state database does not exist: {args.db}", file=sys.stderr)
        return 2
    log_paths = list(args.log_dir.glob("capswriter.log*"))
    captures = read_captures(args.db, since)
    evidence = parse_log_evidence(log_paths, since)
    results = audit(captures, evidence)
    if args.verdict != "all":
        results = [result for result in results if result.verdict == args.verdict]
    if args.json:
        for result in results:
            print(json.dumps(result.to_dict(), ensure_ascii=False, sort_keys=True))
    else:
        print_human(
            results,
            preview_chars=max(40, args.preview_chars),
            summary_only=args.summary_only,
        )
    return 1 if any(result.verdict == "reject" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
