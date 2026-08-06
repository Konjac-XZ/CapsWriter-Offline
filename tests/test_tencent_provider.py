import asyncio
import base64
import hashlib
import hmac
import json
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import numpy as np
import pytest

from src.infra.cosmic import Cosmic
from src.provider.domain import InputMode
from src.transcribe.providers import TencentProvider, make_provider
from src.transcribe.tencent import tencent_transcribe_ws as tencent


def _configure(monkeypatch):
    monkeypatch.setattr(tencent, "get_app_id", lambda: "1250000000")
    monkeypatch.setattr(tencent, "get_secret_id", lambda: "secret-id")
    monkeypatch.setattr(tencent, "get_secret_key", lambda: "secret-key")
    monkeypatch.setattr(tencent, "get_host", lambda: "asr.cloud.tencent.com")
    monkeypatch.setattr(tencent, "get_engine_model_type", lambda: "Hy-ASR-3.0-preview")
    monkeypatch.setattr(
        tencent, "ps_get_int", lambda _key, **kwargs: kwargs.get("default")
    )


def test_provider_aliases_create_dedicated_provider():
    for alias in ("tencent", "tencent-cloud", "hunyuan-asr"):
        provider = make_provider(alias)
        assert isinstance(provider, TencentProvider)
        assert provider.supports_streaming_input() is True
        assert provider.supported_input_modes() == {InputMode.LIVE_AUDIO}


def test_signed_url_follows_tencent_canonical_query(monkeypatch):
    _configure(monkeypatch)

    url = tencent.build_websocket_url(
        "voice-test", timestamp=1_700_000_000, nonce=123456
    )
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    signature = query.pop("signature")[0]
    canonical_query = "&".join(
        f"{key}={values[0]}" for key, values in sorted(query.items())
    )
    source = f"asr.cloud.tencent.com/asr/v2/1250000000?{canonical_query}"
    expected = base64.b64encode(
        hmac.new(b"secret-key", source.encode(), hashlib.sha1).digest()
    ).decode()

    assert parsed.scheme == "wss"
    assert query["engine_model_type"] == ["Hy-ASR-3.0-preview"]
    assert query["voice_format"] == ["1"]
    assert query["needvad"] == ["0"]
    assert signature == expected


def test_pcm_downmixes_48khz_stereo_to_required_16khz_mono():
    audio = np.full((4800, 2), 0.25, dtype=np.float32)

    pcm = tencent.downmix_resample_to_pcm16(audio)

    assert len(pcm) == 3200
    assert np.all(np.frombuffer(pcm, dtype="<i2") == 8191)


def test_response_revisions_replace_same_index_and_join_stable_segments(monkeypatch):
    messages = []

    class Queue:
        async def put(self, message):
            messages.append(message)

    class WebSocket:
        def __init__(self):
            self.responses = iter(
                (
                    {"code": 0, "result": {"index": 0, "voice_text_str": "实时"}},
                    {
                        "code": 0,
                        "result": {"index": 0, "voice_text_str": "实时语音"},
                    },
                    {"code": 0, "result": {"index": 1, "voice_text_str": "识别"}},
                    {"code": 0, "final": 1},
                )
            )

        async def recv(self):
            return json.dumps(next(self.responses))

    original_queue = getattr(Cosmic, "queue_out", None)
    Cosmic.queue_out = cast(Any, Queue())
    monkeypatch.setattr(tencent, "should_emit_deltas", lambda: True)
    monkeypatch.setattr(tencent, "get_chunk_ms", lambda: 200)
    try:
        session = tencent.TencentStreamingSession("task", 1.0)
        session._websocket = WebSocket()
        text = asyncio.run(session._receive_responses())
    finally:
        if original_queue is None:
            del Cosmic.queue_out
        else:
            Cosmic.queue_out = original_queue

    assert text == "实时语音识别"
    assert [message["text"] for message in messages] == [
        "实时",
        "实时语音",
        "实时语音识别",
    ]
    assert messages[-1]["transcript_revision_mode"] == "full_text"


def test_protocol_error_exposes_upstream_code():
    try:
        tencent.parse_message('{"code": 4008, "message": "timeout"}')
    except tencent.TencentProtocolError as exc:
        assert exc.code == 4008
        assert "timeout" in str(exc)
    else:
        raise AssertionError("TencentProtocolError was not raised")


def test_preview_model_rejects_audio_over_sixty_seconds(monkeypatch):
    monkeypatch.setattr(tencent, "get_chunk_ms", lambda: 200)
    session = tencent.TencentStreamingSession("task", 1.0)
    session._websocket = cast(Any, object())
    audio = np.zeros((48_000 * 60 + 3, 1), dtype=np.float32)

    with pytest.raises(ValueError, match="at most 60 seconds"):
        asyncio.run(session.send_audio(audio))
