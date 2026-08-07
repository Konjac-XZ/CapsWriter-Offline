"""Bridge watchdog file-system notifications into an asyncio event loop."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Iterable

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer
from watchdog.observers.api import BaseObserver


class AsyncFileChangeSignal(FileSystemEventHandler):
    """Wake an asyncio task when one of a small set of files changes."""

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        paths: Iterable[Path],
    ) -> None:
        super().__init__()
        resolved_paths = [Path(path).resolve() for path in paths]
        if not resolved_paths:
            raise ValueError("at least one watched path is required")
        parents = {path.parent for path in resolved_paths}
        if len(parents) != 1:
            raise ValueError("all watched paths must share one directory")

        self._loop = loop
        self._event = asyncio.Event()
        self._paths = {os.path.normcase(str(path)) for path in resolved_paths}
        self._directory = resolved_paths[0].parent
        self._observer: BaseObserver | None = None

    def start(self) -> None:
        """Start the native directory observer."""
        if self._observer is not None:
            return
        self._directory.mkdir(parents=True, exist_ok=True)
        observer = Observer()
        observer.schedule(self, str(self._directory), recursive=False)
        observer.start()
        self._observer = observer

    def stop(self) -> None:
        """Stop the observer thread, if one was started."""
        observer = self._observer
        self._observer = None
        if observer is None:
            return
        observer.stop()
        observer.join(timeout=1.0)

    def clear(self) -> None:
        self._event.clear()

    async def wait(self, timeout: float | None = None) -> bool:
        """Wait for a matching change, returning false on a safety timeout."""
        try:
            if timeout is None:
                await self._event.wait()
            else:
                await asyncio.wait_for(self._event.wait(), timeout=timeout)
        except TimeoutError:
            return False
        return True

    def on_any_event(self, event: FileSystemEvent) -> None:
        candidates = [event.src_path]
        destination = getattr(event, "dest_path", "")
        if destination:
            candidates.append(destination)
        if not any(self._matches(path) for path in candidates):
            return
        self._loop.call_soon_threadsafe(self._event.set)

    def _matches(self, path: bytes | str) -> bool:
        try:
            normalized = os.path.normcase(str(Path(os.fsdecode(path)).resolve()))
        except (OSError, RuntimeError):
            return False
        return normalized in self._paths
