from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

ProcessInfo = dict[str, int | str | None]


def _norm_text(value: str | None) -> str:
    return (value or "").replace("/", "\\").lower()


def _is_python_process(name: str | None) -> bool:
    return (name or "").lower() in {"python.exe", "pythonw.exe"}


def _matches_script(command_line: str | None, script_path: Path) -> bool:
    return _norm_text(str(script_path.resolve())) in _norm_text(command_line)


def _matches_executable(command_line: str | None, exe_path: Path) -> bool:
    return _norm_text(str(exe_path.resolve())) in _norm_text(command_line)


def _query_windows_processes() -> list[ProcessInfo]:
    if os.name != "nt":
        return []

    try:
        import pythoncom
        import win32com.client

        pythoncom.CoInitialize()
        try:
            service = win32com.client.GetObject("winmgmts:")
            query = "SELECT ProcessId, ParentProcessId, Name, CommandLine FROM Win32_Process"
            rows = service.ExecQuery(query)
        finally:
            pythoncom.CoUninitialize()
    except Exception:
        return []

    processes: list[ProcessInfo] = []
    for row in rows:
        try:
            pid = int(getattr(row, "ProcessId"))
            ppid = int(getattr(row, "ParentProcessId"))
        except Exception:
            continue
        processes.append(
            {
                "pid": pid,
                "parent_pid": ppid,
                "name": getattr(row, "Name", None),
                "command_line": getattr(row, "CommandLine", None),
            }
        )
    return processes


def current_process_family(processes: list[ProcessInfo] | None = None) -> set[int]:
    """Return current PID plus known ancestors, used to prevent replacement self-kill."""
    if processes is None:
        processes = _query_windows_processes()
    parent_by_pid = {
        int(proc["pid"]): int(proc["parent_pid"])
        for proc in processes
        if proc.get("pid") is not None and proc.get("parent_pid") is not None
    }
    family = {os.getpid()}
    current = os.getpid()
    while current in parent_by_pid:
        parent = parent_by_pid[current]
        if parent in family or parent <= 0:
            break
        family.add(parent)
        current = parent
    return family


def find_python_script_processes(script_path: Path) -> list[ProcessInfo]:
    """Find Python processes whose command line references an exact script path."""
    matches: list[ProcessInfo] = []
    for proc in _query_windows_processes():
        name = str(proc.get("name") or "")
        command_line = str(proc.get("command_line") or "")
        if _is_python_process(name) and _matches_script(command_line, script_path):
            matches.append(proc)
    return matches


def find_executable_processes(exe_path: Path) -> list[ProcessInfo]:
    """Find processes whose command line references an exact executable path."""
    matches: list[ProcessInfo] = []
    expected_name = exe_path.name.lower()
    for proc in _query_windows_processes():
        name = str(proc.get("name") or "")
        command_line = str(proc.get("command_line") or "")
        if (name or "").lower() != expected_name or not _matches_executable(command_line, exe_path):
            continue
        matches.append(proc)
    return matches


def _descendant_order(processes: list[ProcessInfo]) -> list[int]:
    parent_by_pid = {int(proc["pid"]): int(proc["parent_pid"]) for proc in processes}

    def depth(pid: int) -> int:
        current = pid
        seen: set[int] = set()
        result = 0
        while current in parent_by_pid and current not in seen:
            seen.add(current)
            parent = parent_by_pid[current]
            if parent not in parent_by_pid:
                break
            result += 1
            current = parent
        return result

    return sorted(parent_by_pid, key=depth, reverse=True)


def terminate_python_script_processes(script_path: Path, exclude_pid: int | None = None) -> list[int]:
    """Terminate all Python processes running this repository's script path."""
    processes = find_python_script_processes(script_path)
    return terminate_process_matches(processes, exclude_pid=exclude_pid)


def terminate_python_script_basename_processes(
    script_path: Path, exclude_pid: int | None = None
) -> list[int]:
    """Terminate Python script processes for this checkout by command-line basename.

    This is for entry scripts that may be launched by absolute or relative path.
    It still requires the process command line to contain this repository root.
    """
    root_text = _norm_text(str(script_path.resolve().parent))
    basename = script_path.name.lower()
    matches: list[ProcessInfo] = []
    for proc in _query_windows_processes():
        name = str(proc.get("name") or "")
        command_line = str(proc.get("command_line") or "")
        normalized_command = _norm_text(command_line)
        if (
            not _is_python_process(name)
            or root_text not in normalized_command
            or basename not in normalized_command
        ):
            continue
        matches.append(proc)
    return terminate_process_matches(matches, exclude_pid=exclude_pid)


def terminate_executable_processes(exe_path: Path, exclude_pid: int | None = None) -> list[int]:
    """Terminate processes launched from an exact executable path."""
    processes = find_executable_processes(exe_path)
    return terminate_process_matches(processes, exclude_pid=exclude_pid)


def terminate_process_matches(
    processes: list[ProcessInfo], exclude_pid: int | None = None
) -> list[int]:
    protected_pids = current_process_family(processes)
    if exclude_pid is not None:
        protected_pids.add(exclude_pid)
    pids = [pid for pid in _descendant_order(processes) if pid not in protected_pids]
    terminated: list[int] = []

    for pid in pids:
        try:
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                creationflags=subprocess.CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
                timeout=2.0,
                check=False,
            )
            terminated.append(pid)
        except Exception:
            pass

    if terminated:
        time.sleep(0.2)
    return terminated
