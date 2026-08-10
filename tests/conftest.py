from __future__ import annotations

import os


# Pytest exercises Qt widget logic but must never create windows on the desktop.
# This is set during conftest loading, before any test module imports PySide6.
os.environ["QT_QPA_PLATFORM"] = "offscreen"
