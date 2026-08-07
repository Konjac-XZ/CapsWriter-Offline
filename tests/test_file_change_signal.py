from __future__ import annotations

import asyncio
from pathlib import Path

from watchdog.events import FileModifiedEvent, FileMovedEvent

from src.infra.file_change_signal import AsyncFileChangeSignal


def test_async_file_change_signal_matches_atomic_rename_destination(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        target = tmp_path / "request.json"
        signal = AsyncFileChangeSignal(asyncio.get_running_loop(), [target])

        signal.clear()
        signal.on_any_event(
            FileMovedEvent(str(tmp_path / ".request.json.tmp"), str(target))
        )

        assert await signal.wait(timeout=0.1) is True

    asyncio.run(scenario())


def test_async_file_change_signal_ignores_unrelated_files(tmp_path: Path) -> None:
    async def scenario() -> None:
        signal = AsyncFileChangeSignal(
            asyncio.get_running_loop(), [tmp_path / "request.json"]
        )

        signal.clear()
        signal.on_any_event(FileModifiedEvent(str(tmp_path / "other.json")))

        assert await signal.wait(timeout=0.01) is False

    asyncio.run(scenario())


def test_async_file_change_signal_observes_atomic_replace(tmp_path: Path) -> None:
    async def scenario() -> None:
        target = tmp_path / "request.json"
        temporary = tmp_path / ".request.json.tmp"
        signal = AsyncFileChangeSignal(asyncio.get_running_loop(), [target])
        signal.start()
        try:
            signal.clear()
            temporary.write_text("{}", encoding="utf-8")
            temporary.replace(target)
            assert await signal.wait(timeout=2.0) is True
        finally:
            signal.stop()

    asyncio.run(scenario())
