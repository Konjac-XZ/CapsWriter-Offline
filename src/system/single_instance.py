from __future__ import annotations

import os
from pathlib import Path


_LOCK_HANDLES: dict[Path, object] = {}


def _lock_path(root: Path, name: str) -> Path:
    lock_dir = root / ".tmp" / "locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    return lock_dir / f"{name}.lock"


def acquire_single_instance(root: Path, name: str) -> bool:
    """Acquire a process-scoped singleton lock for this repository checkout."""
    path = _lock_path(root, name)
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


def release_single_instance(root: Path, name: str) -> None:
    path = _lock_path(root, name)
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
