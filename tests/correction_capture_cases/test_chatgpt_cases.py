"""Real-world correction-capture cases observed in ChatGPT Desktop."""

from src.tsf_ipc.protocol import TrackingSnapshotKind


def test_2026_08_10_58da4004_erased_slash_command_is_not_a_correction(
    make_bridge_correction_case,
) -> None:
    case = make_bridge_correction_case("ChatGPT.exe")
    committed = "实现这个机制，但是优先实现 commit 锁，而不是 Artifact Fingerprint 锁。"
    session_id = case.commit(committed)
    case.observe(
        session_id,
        13,
        TrackingSnapshotKind.BASELINE,
        before="",
        tracked=committed,
        after="\n",
    )

    # The slash command was typed, erased, attempted again, and erased again.
    # Only the final stable observation is externally meaningful.
    observations = [
        committed + suffix
        for suffix in (
            "/",
            "/g",
            "/go",
            "/goa",
            "/goal",
            "/goa",
            "/go",
            "/g",
            "/",
            "",
            " ",
            " /",
            " /g",
            " /go",
            " /goa",
            " /goal",
            " ",
            "",
        )
    ]
    for revision, tracked in enumerate(observations, start=14):
        case.observe(
            session_id,
            revision,
            TrackingSnapshotKind.CURRENT,
            before="",
            tracked=tracked,
            after="\n\n",
        )
    case.settle(session_id)

    assert case.updates == []
