"""Versioned control and status protocol for native GUI shells."""

from .service import (
    GUI_PROTOCOL_VERSION,
    gui_protocol_enabled,
    publish_gui_snapshots,
    run_gui_command_loop,
)

__all__ = [
    "GUI_PROTOCOL_VERSION",
    "gui_protocol_enabled",
    "publish_gui_snapshots",
    "run_gui_command_loop",
]
