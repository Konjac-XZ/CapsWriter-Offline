import asyncio
import math
import threading

import numpy as np

from src.infra.config import config as Config
from src.infra.cosmic import Cosmic
from src.infra.gui_output import gui_event


OVERLAY_LEVEL_INTERVAL = 0.05
OVERLAY_LEVEL_FLOOR_DB = -55.0
OVERLAY_LEVEL_CEILING_DB = 0.0

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
    while True:
        await asyncio.sleep(OVERLAY_LEVEL_INTERVAL)
        if not Config.show_listening_overlay or not Cosmic.on:
            continue

        level = _take_latest_level()
        if level is None:
            continue

        gui_event("status_overlay", action="level", level=level)
