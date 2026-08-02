from __future__ import annotations

import os
import time
from pathlib import Path
from typing import BinaryIO, Callable


_LOCK_HANDLES: dict[Path, BinaryIO] = {}


def _slot_path(root: Path, role: str) -> Path:
    lock_dir = root / ".tmp" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{role}.lock"


def acquire_startup_slot(root: Path, role: str) -> bool:
    """Acquire the runtime slot for the process that will own this role."""
    path = _slot_path(root, role)
    handle = path.open("a+b")
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        return False

    handle.seek(0)
    handle.truncate()
    handle.write(str(os.getpid()).encode("ascii"))
    handle.flush()
    _LOCK_HANDLES[path] = handle
    return True


def prepare_replacement_startup(
    root: Path,
    role: str,
    replace_existing: Callable[[], object],
    attempts: int = 10,
    delay_s: float = 0.2,
) -> bool:
    """Take over a role, replacing older matching processes when needed."""
    if acquire_startup_slot(root, role):
        return True

    replace_existing()
    for _ in range(max(1, attempts)):
        if acquire_startup_slot(root, role):
            return True
        time.sleep(delay_s)
    return False


def release_startup_slot(root: Path, role: str) -> None:
    path = _slot_path(root, role)
    handle = _LOCK_HANDLES.pop(path, None)
    if handle is None:
        return
    try:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    try:
        handle.close()
    except Exception:
        pass
