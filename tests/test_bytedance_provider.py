import asyncio
import gzip
import json
import struct
from typing import Any, cast

import numpy as np
import pytest

from src.infra.cosmic import Cosmic
from src.transcribe.bytedance import bytedance_transcribe_ws as bytedance
from src.transcribe.providers import ByteDanceProvider, make_provider


def _server_response(payload: dict[str, Any], *, final: bool = False) -> bytes:
    data = gzip.compress(json.dumps(payload).encode())
    flags = 0x3 if final else 0x1
    sequence = -2 if final else 1
    return (
        bytes((0x11, 0x90 | flags, 0x11, 0x00))
        + struct.pack(">iI", sequence, len(data))
        + data
    )


def test_provider_aliases_create_dedicated_provider():
    for alias in ("bytedance", "doubao", "volcengine", "volc"):
        assert isinstance(make_provider(alias), ByteDanceProvider)


def test_headers_prefer_new_console_api_key(monkeypatch):
    monkeypatch.setattr(bytedance, "get_api_key", lambda: "key-test")
    monkeypatch.setattr(bytedance, "get_resource_id", lambda: "volc.seedasr.test")

    headers = bytedance.build_headers("connect-test")

    assert headers == {
        "X-Api-Key": "key-test",
        "X-Api-Resource-Id": "volc.seedasr.test",
        "X-Api-Connect-Id": "connect-test",
    }


def test_request_uses_asr_2_resource_defaults_and_pcm(monkeypatch):
    monkeypatch.setattr(
        bytedance, "ps_get_bool", lambda key, **kwargs: kwargs["default"]
    )
    monkeypatch.setattr(
        bytedance, "ps_get_int", lambda key, **kwargs: kwargs["default"]
    )
    monkeypatch.setattr(
        bytedance, "ps_get_str", lambda key, **kwargs: kwargs.get("default")
    )

    payload = bytedance.build_request_payload()
    frame = bytedance.build_full_request(payload)
    size = struct.unpack_from(">I", frame, 4)[0]
    decoded = json.loads(gzip.decompress(frame[8 : 8 + size]))

    assert frame[:4] == bytes((0x11, 0x10, 0x11, 0x00))
    assert decoded["audio"] == {
        "format": "pcm",
        "codec": "raw",
        "rate": 16000,
        "bits": 16,
        "channel": 1,
    }
    assert decoded["request"]["model_name"] == "bigmodel"
    assert decoded["request"]["ssd_version"] == "200"
    assert decoded["request"]["enable_nonstream"] is True
    assert decoded["request"]["result_type"] == "full"


def test_audio_final_packet_uses_last_packet_flag():
    regular = bytedance.build_audio_request(b"pcm")
    final = bytedance.build_audio_request(b"", final=True)

    assert regular[1] == 0x20
    assert final[1] == 0x22
    assert gzip.decompress(final[8:]) == b""


def test_parse_server_response_and_error():
    payload, final, sequence = bytedance.parse_server_message(
        _server_response({"result": {"text": "豆包识别"}}, final=True)
    )

    assert bytedance.extract_transcript(payload) == "豆包识别"
    assert final is True
    assert sequence == -2

    detail = json.dumps({"message": "bad request"}).encode()
    error = (
        bytes((0x11, 0xF0, 0x10, 0x00))
        + struct.pack(">II", 45000001, len(detail))
        + detail
    )
    with pytest.raises(bytedance.ByteDanceProtocolError, match="45000001"):
        bytedance.parse_server_message(error)


def test_pcm_downmixes_48khz_stereo_to_16khz_mono():
    audio = np.full((4800, 2), 0.25, dtype=np.float32)

    pcm = bytedance.downmix_resample_to_pcm16(audio)

    assert len(pcm) == 3200
    samples = np.frombuffer(pcm, dtype="<i2")
    assert np.all(samples == 8191)


def test_response_revisions_are_emitted_as_full_text(monkeypatch):
    messages = []

    class Queue:
        async def put(self, message):
            messages.append(message)

    original_queue = getattr(Cosmic, "queue_out", None)
    Cosmic.queue_out = cast(Any, Queue())
    monkeypatch.setattr(bytedance, "should_emit_deltas", lambda: True)
    monkeypatch.setattr(bytedance, "get_sample_rate", lambda: 16000)
    monkeypatch.setattr(bytedance, "get_chunk_ms", lambda: 200)
    try:
        session = bytedance.ByteDanceStreamingSession("task-1", 1.0)
        session.t_submit = 2.0
        asyncio.run(session._handle_text("累计全文"))
    finally:
        if original_queue is None:
            del Cosmic.queue_out
        else:
            Cosmic.queue_out = original_queue

    assert messages[0]["text"] == "累计全文"
    assert messages[0]["transcript_revision_mode"] == "full_text"
    assert messages[0]["is_final"] is False
