from tools.audit_tsf_corrections import (
    CorrectionCapture,
    LogEvidence,
    classify_capture,
    parse_log_evidence,
)


def _capture(committed: str, corrected: str) -> CorrectionCapture:
    return CorrectionCapture(
        id=1,
        session_id="12345678-1234-1234-1234-123456789abc",
        asr_text=committed,
        committed_text=committed,
        corrected_text=corrected,
        event_revision=1,
        processed_revision=0,
        first_observed_at=1.0,
        last_observed_at=2.0,
        preference_evidence_count=0,
    )


def test_rejects_adjacent_document_line_captured_as_single_line_edit():
    result = classify_capture(
        _capture(
            "mongoose #3 未拒绝包含多个不同 Content-Length 值的请求",
            "求\nmongoose #3 未拒绝包含多个不同 Content-Length 值的",
        ),
        LogEvidence(prefix_window_full=True, alignment_score=0.96),
    )

    assert result.verdict == "reject"
    assert any("相邻文档" in reason for reason in result.reasons)


def test_keeps_small_local_wording_correction():
    result = classify_capture(
        _capture(
            "没看到上游请求当中有负载相关的信息。",
            "没看到上游请求当中有附带相关的信息。",
        )
    )

    assert result.verdict == "pass"


def test_clipped_but_similar_capture_requires_review():
    result = classify_capture(
        _capture("这是原来的句子。", "这是原来的句子！"),
        LogEvidence(prefix_window_full=True, alignment_score=0.95),
    )

    assert result.verdict == "review"
    assert any("截断边界" in reason for reason in result.reasons)


def test_appended_followup_sentence_requires_review():
    result = classify_capture(
        _capture(
            "但是对于这个项目，我不熟",
            "但是对于这个项目，我不熟。这个能整体下载吗？",
        )
    )

    assert result.verdict == "review"
    assert any("继续吸收" in reason for reason in result.reasons)


def test_word_initial_range_damage_requires_review():
    result = classify_capture(
        _capture(
            "libcoap #8 报头中声明的 Token 长度不匹配",
            "bcoap #8 报头中声明的 Token 长度不匹配",
        )
    )

    assert result.verdict == "review"
    assert any("词内开始" in reason for reason in result.reasons)


def test_parses_snapshot_and_reconciliation_log_evidence(tmp_path):
    log = tmp_path / "capswriter.log"
    log.write_text(
        "2026-08-10 15:42:27 INFO     123 capswriter.tsf.bridge: "
        "TSF host selected session=10f0c0ae pid=1 process=Typora.exe processor=default\n"
        "2026-08-10 15:42:27 INFO     123 capswriter.tsf.bridge: "
        "TSF tracking snapshot session=10f0c0ae revision=12 kind=current "
        "target_start=1024 target_end=1065 text='前文\\n捕捉文本'\n"
        "2026-08-10 15:42:28 INFO     123 capswriter.tsf.bridge: "
        "TSF tracking reconciliation session=10f0c0ae mode=incremental "
        "alignment_score=0.960 history_matched=True text='错误捕捉'\n",
        encoding="utf-8",
    )

    evidence = parse_log_evidence([log])["10f0c0ae"]

    assert evidence.process == "Typora.exe"
    assert evidence.prefix_window_full is True
    assert evidence.alignment_score == 0.96
    assert evidence.reconciliation_text == "错误捕捉"
