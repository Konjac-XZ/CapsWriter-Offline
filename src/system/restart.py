import os
import subprocess
from pathlib import Path
from time import sleep

from src.system.check_process import check_process
from src.infra.env_loader import load_dotenv_files


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
    cwd = Path(__file__).resolve().parent.parent.parent
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
