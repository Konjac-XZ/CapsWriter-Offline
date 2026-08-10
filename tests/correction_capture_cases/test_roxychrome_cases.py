"""Real-world correction-capture cases observed in RoxyChrome."""

from src.tsf_ipc.protocol import TrackingSnapshotKind


def test_2026_08_10_4b0673fb_followup_question_is_not_previous_correction(
    make_bridge_correction_case,
) -> None:
    case = make_bridge_correction_case("RoxyChrome.exe")
    committed = "但是对于这个 lightweight IP，我是字面意思上的不熟"
    expected = committed + "。"
    before = "https://savannah.nongnu.org/bugs/ 前一段讨论。"
    session_id = case.commit(committed)
    case.observe(
        session_id,
        10,
        TrackingSnapshotKind.BASELINE,
        before=before,
        tracked=committed,
        after="\n\n",
    )

    observations = [
        expected,
        expected + "这个",
        expected + "这个能",
        expected + "这个能整体",
        expected + "这个能整体打包",
        expected + "这个能整体打包下载",
        expected + "这个能整体打包下载吗",
        expected + "这个能整体打包下载吗？",
    ]
    for revision, tracked in enumerate(observations, start=11):
        case.observe(
            session_id,
            revision,
            TrackingSnapshotKind.CURRENT,
            before=before,
            tracked=tracked,
            after="\n\n",
        )
    case.settle(session_id)

    assert case.updates[-1] == (session_id, expected)
