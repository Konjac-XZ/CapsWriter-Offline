from __future__ import annotations

import argparse
import csv
import ctypes
import hashlib
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Sequence


CLSID = "{B635F2D7-83A5-462D-A3CE-DA8284B49D93}"
PROFILE_GUID = "{68CE1F8D-F760-4D4D-A738-BC80C01E6726}"
DLL_NAME = "CapsWriterSpeechTip.dll"
PROJECT_DIR = Path(__file__).resolve().parent
REGISTRY_KEY = rf"Software\Classes\CLSID\{CLSID}\InprocServer32"
PROFILE_KEY = (
    rf"Software\Microsoft\CTF\TIP\{CLSID}"
    rf"\LanguageProfile\0x00000804\{PROFILE_GUID}"
)


class WorkflowError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Architecture:
    name: str
    registry_view: int
    regsvr32: Path
    build_preset: str
    build_dir: str
    build_dll: Path


@dataclass(frozen=True, slots=True)
class RegistrationState:
    architecture: Architecture
    registered_path: Path | None


def architectures() -> tuple[Architecture, Architecture]:
    system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    return (
        Architecture(
            name="x64",
            registry_view=0x0100,
            regsvr32=system_root / "System32" / "regsvr32.exe",
            build_preset="x64-release",
            build_dir="build-x64",
            build_dll=PROJECT_DIR / "build-x64" / "Release" / DLL_NAME,
        ),
        Architecture(
            name="x86",
            registry_view=0x0200,
            regsvr32=system_root / "SysWOW64" / "regsvr32.exe",
            build_preset="x86-release",
            build_dir="build-x86",
            build_dll=PROJECT_DIR / "build-x86" / "Release" / DLL_NAME,
        ),
    )


def normalized_path(path: Path) -> str:
    return os.path.normcase(os.path.abspath(path))


def command_text(command: Sequence[os.PathLike[str] | str]) -> str:
    return subprocess.list2cmdline([os.fspath(part) for part in command])


def run_command(
    command: Sequence[os.PathLike[str] | str],
    *,
    dry_run: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str] | None:
    print(f"> {command_text(command)}")
    if dry_run:
        return None
    return subprocess.run(
        [os.fspath(part) for part in command],
        cwd=PROJECT_DIR,
        check=check,
        text=True,
    )


def read_registration(architecture: Architecture) -> RegistrationState:
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            REGISTRY_KEY,
            0,
            winreg.KEY_READ | architecture.registry_view,
        ) as key:
            value, value_type = winreg.QueryValueEx(key, "")
    except FileNotFoundError:
        return RegistrationState(architecture, None)
    if value_type not in (winreg.REG_SZ, winreg.REG_EXPAND_SZ) or not value:
        raise WorkflowError(
            f"{architecture.name} registration has an invalid default value"
        )
    if value_type == winreg.REG_EXPAND_SZ:
        value = os.path.expandvars(value)
    return RegistrationState(architecture, Path(value))


def registration_states() -> tuple[RegistrationState, RegistrationState]:
    x64, x86 = architectures()
    return read_registration(x64), read_registration(x86)


def profile_enabled() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, PROFILE_KEY) as key:
            value, value_type = winreg.QueryValueEx(key, "Enable")
    except FileNotFoundError:
        return False
    return value_type == winreg.REG_DWORD and value == 1


def loaded_hosts() -> list[str]:
    result = subprocess.run(
        ["tasklist.exe", "/m", DLL_NAME, "/fo", "csv", "/nh"],
        check=False,
        capture_output=True,
        text=True,
    )
    hosts: list[str] = []
    for row in csv.reader(result.stdout.splitlines()):
        if row and row[0].lower().endswith(".exe"):
            hosts.append(", ".join(row[:2]))
    return hosts


def print_status() -> None:
    print("TSF Speech TIP registration status:")
    for state in registration_states():
        if state.registered_path is None:
            status = "not registered"
        else:
            suffix = "" if state.registered_path.exists() else " [DLL missing]"
            status = f"registered: {state.registered_path}{suffix}"
        print(f"  {state.architecture.name}: {status}")
    print(
        f"  TSF language profile: {'enabled' if profile_enabled() else 'not enabled'}"
    )
    hosts = loaded_hosts()
    if hosts:
        print("Loaded by:")
        for host in hosts:
            print(f"  {host}")
    else:
        print("Loaded by: no running process")


def require_supported_host() -> None:
    if platform.system() != "Windows":
        raise WorkflowError("TSF registration is supported only on Windows")


def require_elevated() -> None:
    if not bool(ctypes.windll.shell32.IsUserAnAdmin()):
        raise WorkflowError(
            "Administrator privileges are required before changing TSF categories. "
            "Run this command through sudo or an elevated terminal."
        )


def require_recoverable_registration(states: Sequence[RegistrationState]) -> None:
    for state in states:
        registered = state.registered_path
        if registered is not None and not registered.exists():
            raise WorkflowError(
                f"Cannot safely update {state.architecture.name}: its registered "
                f"DLL is missing at {registered}, so rollback would be impossible"
            )


def report_loaded_hosts() -> None:
    hosts = loaded_hosts()
    if not hosts:
        return
    print(
        "INFO: these processes keep using their already-loaded DLL until they "
        "restart; registration will point new processes at the new version:"
    )
    for host in hosts:
        print(f"  {host}")


def build_and_test_commands() -> list[list[str]]:
    commands: list[list[str]] = []
    for architecture in architectures():
        configure = "vs2022-x64" if architecture.name == "x64" else "vs2022-x86"
        commands.extend(
            [
                ["cmake", "--preset", configure],
                ["cmake", "--build", "--preset", architecture.build_preset],
                [
                    "ctest",
                    "--test-dir",
                    architecture.build_dir,
                    "-C",
                    "Release",
                    "--output-on-failure",
                ],
            ]
        )
    return commands


def deployment_id() -> str:
    digest = hashlib.sha256()
    for architecture in architectures():
        if not architecture.build_dll.exists():
            raise WorkflowError(
                f"{architecture.name} Release DLL not found: {architecture.build_dll}"
            )
        digest.update(architecture.name.encode("ascii"))
        digest.update(architecture.build_dll.read_bytes())
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{digest.hexdigest()[:12]}-{os.getpid()}"


def deployment_paths(version: str) -> dict[str, Path]:
    root = PROJECT_DIR / "installed" / "versions" / version
    return {
        architecture.name: root / architecture.name / DLL_NAME
        for architecture in architectures()
    }


def regsvr32_command(
    architecture: Architecture, dll: Path, *, unregister: bool = False
) -> list[os.PathLike[str] | str]:
    command: list[os.PathLike[str] | str] = [architecture.regsvr32, "/s"]
    if unregister:
        command.append("/u")
    command.append(dll)
    return command


def verify_registered(expected_paths: dict[str, Path]) -> None:
    errors: list[str] = []
    for state in registration_states():
        expected = expected_paths[state.architecture.name]
        if state.registered_path is None:
            errors.append(f"{state.architecture.name} is not registered")
        elif normalized_path(state.registered_path) != normalized_path(expected):
            errors.append(
                f"{state.architecture.name} points to {state.registered_path}, "
                f"expected {expected}"
            )
    if not profile_enabled():
        errors.append("the zh-CN TSF language profile is not enabled")
    if errors:
        raise WorkflowError(
            "Registration verification failed:\n  " + "\n  ".join(errors)
        )


def verify_unregistered() -> None:
    remaining = [
        state.architecture.name
        for state in registration_states()
        if state.registered_path is not None
    ]
    if remaining:
        raise WorkflowError(
            "Unregistration verification failed for: " + ", ".join(remaining)
        )
    if profile_enabled():
        raise WorkflowError("Unregistration left the TSF language profile enabled")


def rollback_install(
    previous: Sequence[RegistrationState], deployed: dict[str, Path]
) -> None:
    print("Registration failed; restoring the previous registry targets...")
    for architecture in reversed(architectures()):
        new_dll = deployed[architecture.name]
        if new_dll.exists():
            run_command(
                regsvr32_command(architecture, new_dll, unregister=True),
                check=False,
            )

    for state in previous:
        if state.registered_path is not None:
            run_command(
                regsvr32_command(state.architecture, state.registered_path),
                check=False,
            )

    deployment_root = next(iter(deployed.values())).parents[1]
    if not deployment_root.exists():
        return
    try:
        shutil.rmtree(deployment_root)
    except OSError as exc:
        print(f"WARNING: could not remove failed deployment {deployment_root}: {exc}")


def install(*, skip_build: bool, dry_run: bool) -> None:
    if not dry_run:
        require_elevated()
    previous = registration_states()
    require_recoverable_registration(previous)
    report_loaded_hosts()

    build_commands: list[Sequence[os.PathLike[str] | str]] = []
    if not skip_build:
        build_commands.extend(build_and_test_commands())
    command_processor = os.environ.get("ComSpec", "cmd.exe")

    if dry_run:
        for command in build_commands:
            run_command(command, dry_run=True)
        preview_root = PROJECT_DIR / "installed" / "versions" / "<version-id>"
        preview_paths = {
            architecture.name: preview_root / architecture.name / DLL_NAME
            for architecture in architectures()
        }
        print("> copy x64 and x86 Release DLLs into a new version directory")
        run_command(
            [
                command_processor,
                "/d",
                "/c",
                PROJECT_DIR / "signing" / "sign.cmd",
                preview_paths["x64"],
                preview_paths["x86"],
            ],
            dry_run=True,
        )
        for architecture in architectures():
            run_command(
                regsvr32_command(architecture, preview_paths[architecture.name]),
                dry_run=True,
            )
        print("> verify x64/x86 HKCU COM paths and the TSF language profile")
        return

    for command in build_commands:
        run_command(command)

    version = deployment_id()
    deployed = deployment_paths(version)
    deployment_root = next(iter(deployed.values())).parents[1]
    try:
        for architecture in architectures():
            destination = deployed[architecture.name]
            destination.parent.mkdir(parents=True, exist_ok=False)
            shutil.copy2(architecture.build_dll, destination)

        run_command(
            [
                command_processor,
                "/d",
                "/c",
                PROJECT_DIR / "signing" / "sign.cmd",
                deployed["x64"],
                deployed["x86"],
            ]
        )
        for architecture in architectures():
            run_command(regsvr32_command(architecture, deployed[architecture.name]))
        verify_registered(deployed)
    except Exception:
        rollback_install(previous, deployed)
        raise

    print("x64 and x86 TSF DLLs were built, tested, signed, registered, and verified.")
    print(f"Versioned deployment: {deployment_root}")
    print("Already-running applications keep the old DLL until they restart.")


def uninstall(*, dry_run: bool) -> None:
    if not dry_run:
        require_elevated()
    states = registration_states()
    require_recoverable_registration(states)
    report_loaded_hosts()
    for state in reversed(states):
        if state.registered_path is None:
            print(f"{state.architecture.name}: already unregistered")
            continue
        assert state.registered_path is not None
        run_command(
            regsvr32_command(
                state.architecture,
                state.registered_path,
                unregister=True,
            ),
            dry_run=dry_run,
        )
    if not dry_run:
        verify_unregistered()
        print("x64 and x86 TSF registrations were removed and verified.")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build, test, sign, register, verify, or unregister the TSF TIP."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    install_parser = subparsers.add_parser(
        "install",
        help="build, test, sign, deploy side by side, register, and verify both DLLs",
    )
    install_parser.add_argument(
        "--skip-build",
        action="store_true",
        help="reuse the existing x64/x86 Release build outputs",
    )
    install_parser.add_argument(
        "--dry-run", action="store_true", help="show the planned commands only"
    )
    uninstall_parser = subparsers.add_parser(
        "uninstall", help="unregister both DLLs without deleting deployed versions"
    )
    uninstall_parser.add_argument("--dry-run", action="store_true")
    subparsers.add_parser("status", help="show both registry views and loaded hosts")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        require_supported_host()
        args = parse_args(sys.argv[1:] if argv is None else argv)
        if args.command == "status":
            print_status()
        elif args.command == "install":
            install(skip_build=args.skip_build, dry_run=args.dry_run)
        elif args.command == "uninstall":
            uninstall(dry_run=args.dry_run)
        return 0
    except (WorkflowError, OSError, subprocess.CalledProcessError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
