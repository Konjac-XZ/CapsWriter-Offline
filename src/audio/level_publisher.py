import asyncio
import math
import threading
import time

import numpy as np

from src.infra.config import config as Config
from src.infra.cosmic import Cosmic
from src.infra.gui_output import gui_event


OVERLAY_LEVEL_INTERVAL = 0.1
OVERLAY_LEVEL_FLOOR_DB = -55.0
OVERLAY_LEVEL_CEILING_DB = 0.0
OVERLAY_LEVEL_MIN_DELTA = 0.015

_level_lock = threading.Lock()
_latest_level: float | None = None


def update_latest_level(indata: np.ndarray) -> None:
    if not Config.show_listening_overlay:
        return
    try:
        rms = float(np.sqrt(np.mean(np.square(indata, dtype=np.float32))))
        db = 20.0 * math.log10(max(rms, 1e-8))
        level = (db - OVERLAY_LEVEL_FLOOR_DB) / (
            OVERLAY_LEVEL_CEILING_DB - OVERLAY_LEVEL_FLOOR_DB
        )
        level = max(0.0, min(1.0, level))
    except Exception:
        return

    global _latest_level
    with _level_lock:
        _latest_level = level


def _take_latest_level() -> float | None:
    with _level_lock:
        return _latest_level


async def publish_overlay_levels() -> None:
    last_sent_level: float | None = None
    last_sent_at = 0.0
    while True:
        await asyncio.sleep(OVERLAY_LEVEL_INTERVAL)
        if not Config.show_listening_overlay or not Cosmic.on:
            last_sent_level = None
            continue

        level = _take_latest_level()
        if level is None:
            continue
        now = time.monotonic()
        if (
            last_sent_level is not None
            and abs(level - last_sent_level) < OVERLAY_LEVEL_MIN_DELTA
            and now - last_sent_at < 0.5
        ):
            continue

        gui_event("status_overlay", action="level", level=level)
        last_sent_level = level
        last_sent_at = now
