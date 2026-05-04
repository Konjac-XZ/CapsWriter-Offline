from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import cast

from src.infra.env_loader import load_dotenv_files


if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    ROOT: Path = Path(sys.executable).resolve().parent
    BUNDLE_ROOT: Path = Path(cast(str, getattr(sys, "_MEIPASS")))
else:
    ROOT = Path(__file__).resolve().parents[2]
    BUNDLE_ROOT = ROOT


def ensure_project_cwd() -> None:
    try:
        os.chdir(str(ROOT))
    except Exception:
        pass


def load_startup_env() -> None:
    try:
        load_dotenv_files()
    except Exception:
        pass


def resolve_pythonw_client() -> str | None:
    """Return a usable Python interpreter for client child processes."""
    candidates: list[Path] = [
        ROOT / ".venv" / "Scripts" / "pythonw.exe",
        ROOT / ".venv" / "Scripts" / "python.exe",
    ]
    if sys.executable:
        candidates.append(Path(sys.executable))
    for path in candidates:
        if path.exists():
            return str(path)
    return None


def resolve_console_python() -> str:
    python_exe = ROOT / ".venv" / "Scripts" / "python.exe"
    if python_exe.exists():
        return str(python_exe)
    return sys.executable


def core_client_script_path() -> Path:
    return ROOT / "core_client.py"


def availability_test_script_path() -> Path:
    return ROOT / "src" / "provider" / "run_availability_test.py"


def restart_script_path() -> Path:
    return ROOT / "src" / "system" / "restart.py"


def theme_css_path() -> Path:
    return BUNDLE_ROOT / "src" / "client_gui_theme_custom.css"


def client_icon_path() -> Path:
    return BUNDLE_ROOT / "assets" / "client-icon.ico"


def read_file_list(file_list_path: Path) -> list[Path]:
    with file_list_path.open("r", encoding="utf-8") as handle:
        return [Path(line.strip()) for line in handle if line.strip()]
