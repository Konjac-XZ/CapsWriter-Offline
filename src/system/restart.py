import os
import subprocess
from pathlib import Path
from time import sleep

from src.infra.env_loader import load_dotenv_files
from src.system.process_cleanup import (
    find_executable_processes,
    terminate_executable_processes,
    terminate_python_script_processes,
)


ROOT = Path(__file__).resolve().parents[2]


def stop_exe(exe_name: str):
    exe_path = ROOT / exe_name
    print(f"Stopping {exe_path}")
    if exe_path.exists():
        terminate_executable_processes(exe_path, exclude_pid=os.getpid())
    else:
        print(f"Skip missing executable: {exe_path}")


def start_exe(exe_name: str):
    print(f"Starting {exe_name}")
    load_dotenv_files()
    env = os.environ.copy()

    exe_path = ROOT / exe_name
    if not exe_path.exists():
        print(f"Skip missing executable: {exe_path}")
        return

    try:
        subprocess.Popen(
            [str(exe_path)],
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            text=True,
            env=env,
        )
    except FileNotFoundError:
        subprocess.Popen(
            f'"{exe_name}"',
            cwd=str(ROOT),
            creationflags=subprocess.CREATE_NO_WINDOW,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
            text=True,
            env=env,
        )
    sleep(1)
    if not find_executable_processes(exe_path):
        start_exe(exe_name)


def restart_exe(exe_name: str):
    stop_exe(exe_name)
    start_exe(exe_name)


def stop_client():
    terminate_python_script_processes(ROOT / "core_client.py", exclude_pid=os.getpid())

    exe_name_list = [
        "start_client_gui_admin.exe",
        "start_client_gui.exe",
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
    if find_executable_processes(ROOT / "start_client_gui_admin.exe"):
        restart_client_admin()
    else:
        restart_client()
