from __future__ import annotations

from src.audio import stream as audio_stream
from src.infra.cosmic import Cosmic


class FakeStream:
    def __init__(self, *, start_error: Exception | None = None) -> None:
        self.close_calls = 0
        self.start_calls = 0
        self._start_error = start_error

    def close(self) -> None:
        self.close_calls += 1

    def start(self) -> None:
        self.start_calls += 1
        if self._start_error is not None:
            raise self._start_error


def test_stream_reopen_uses_stream_only_fast_path(monkeypatch) -> None:
    old_stream = FakeStream()
    new_stream = FakeStream()
    Cosmic.stream = old_stream

    monkeypatch.setattr(audio_stream, "stream_open", lambda: new_stream)
    reload_calls: list[None] = []
    monkeypatch.setattr(
        audio_stream, "_reload_portaudio", lambda: reload_calls.append(None)
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr(audio_stream.time, "sleep", sleep_calls.append)

    audio_stream.stream_reopen(start=True)

    assert old_stream.close_calls == 1
    assert new_stream.start_calls == 1
    assert Cosmic.stream is new_stream
    assert reload_calls == []
    assert sleep_calls == []


def test_stream_reopen_refreshes_portaudio_after_open_failure(monkeypatch) -> None:
    old_stream = FakeStream()
    recovered_stream = FakeStream()
    Cosmic.stream = old_stream

    open_results = iter((RuntimeError("device unavailable"), recovered_stream))

    def fake_stream_open() -> FakeStream:
        result = next(open_results)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(audio_stream, "stream_open", fake_stream_open)
    reload_calls: list[None] = []
    monkeypatch.setattr(
        audio_stream, "_reload_portaudio", lambda: reload_calls.append(None)
    )
    sleep_calls: list[float] = []
    monkeypatch.setattr(audio_stream.time, "sleep", sleep_calls.append)

    audio_stream.stream_reopen(start=True)

    assert old_stream.close_calls == 1
    assert recovered_stream.start_calls == 1
    assert Cosmic.stream is recovered_stream
    assert reload_calls == [None]
    assert sleep_calls == [0.1]


def test_stream_reopen_refreshes_portaudio_after_start_failure(monkeypatch) -> None:
    old_stream = FakeStream()
    failed_stream = FakeStream(start_error=RuntimeError("start failed"))
    recovered_stream = FakeStream()
    Cosmic.stream = old_stream

    streams = iter((failed_stream, recovered_stream))
    monkeypatch.setattr(audio_stream, "stream_open", lambda: next(streams))
    reload_calls: list[None] = []
    monkeypatch.setattr(
        audio_stream, "_reload_portaudio", lambda: reload_calls.append(None)
    )
    monkeypatch.setattr(audio_stream.time, "sleep", lambda _seconds: None)

    audio_stream.stream_reopen(start=True)

    assert old_stream.close_calls == 1
    assert failed_stream.start_calls == 1
    assert failed_stream.close_calls == 1
    assert recovered_stream.start_calls == 1
    assert Cosmic.stream is recovered_stream
    assert reload_calls == [None]
