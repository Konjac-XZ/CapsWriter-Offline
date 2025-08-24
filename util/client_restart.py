import os
import re
import subprocess
from pathlib import Path
from time import sleep

from util.check_process import check_process


def load_dotenv_files():
    """Load .env and .env.local from the repository root into os.environ.

    This is a simple loader that supports lines like KEY=VALUE, with optional
    single or double quotes around the value. Lines beginning with # and empty
    lines are ignored. .env.local overrides .env when both exist.
    """
    root = Path(__file__).resolve().parent.parent
    candidates = [root / ".env", root / ".env.local"]
    loaded = {}

    for path in candidates:
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            # fallback to default encoding
            text = path.read_text()

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()

            # If quoted, remove surrounding quotes and keep interior characters as-is
            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            else:
                # remove inline comments after unquoted value
                if "#" in val:
                    val = val.split("#", 1)[0].strip()

            # Expand simple variable references like $VAR or ${VAR}
            def _replace_var(m):
                name = m.group(1) or m.group(2)
                return os.environ.get(name, "")

            val = re.sub(r"\$(?:{([^}]+)}|([A-Za-z_][A-Za-z0-9_]*))", _replace_var, val)

            os.environ[key] = val
            loaded[key] = val

    if loaded:
        print(f"Loaded env vars: {', '.join(loaded.keys())}")
    return loaded


def stop_exe(exe_name: str):
    print(f"Stopping {exe_name}")
    subprocess.Popen(
        f"taskkill /IM {exe_name} /F",
        creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        text=True,
    )
    sleep(1)
    if check_process(exe_name):
        stop_exe(exe_name)
    else:
        return


def start_exe(exe_name: str):
    print(f"Starting {exe_name}")
    # Refresh environment from .env files before launching so the started
    # process inherits the latest values.
    load_dotenv_files()

    # Make a copy of the current environment and pass it explicitly to Popen.
    env = os.environ.copy()

    # Launch directly (avoid 'start' which spawns via cmd and may drop env/cwd)
    cwd = Path(__file__).resolve().parent.parent
    exe_path = cwd / exe_name
    try:
        subprocess.Popen(
            [str(exe_path)],
            cwd=str(cwd),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            text=True,
            env=env,
        )
    except FileNotFoundError:
        # Fallback to shell invocation if needed
        subprocess.Popen(
            f'"{exe_name}"',
            cwd=str(cwd),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            text=True,
            env=env,
        )
    sleep(1)
    if not check_process(exe_name):
        start_exe(exe_name)
    else:
        return


def restart_exe(exe_name: str):
    stop_exe(exe_name)
    start_exe(exe_name)


def stop_client():
    exe_name_list = [
        "start_client_gui_admin.exe",
        "start_client_gui.exe",
        "pythonw_CapsWriter_Client.exe",
        "hint_while_recording.exe",
    ]

    for exe_name in exe_name_list:
        stop_exe(exe_name)


def restart_client_admin():
    stop_client()
    start_exe("start_client_gui_admin.exe")


def restart_client():
    stop_client()
    start_exe("start_client_gui.exe")


if __name__ == "__main__":
    if check_process("start_client_gui_admin.exe"):
        restart_client_admin()
    else:
        restart_client()
