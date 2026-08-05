from __future__ import annotations

import argparse
import csv
import hashlib
import os
import platform
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import psutil


DLL_NAME = "CapsWriterSpeechTip.dll"
PROJECT_DIR = Path(__file__).resolve().parent
VERSIONS_DIR = PROJECT_DIR / "installed" / "versions"
ARCHITECTURES = ("x64", "x86")
VERSION_PATTERN = re.compile(r"^(\d{8})-(\d{6})-(.+)$")


class InspectionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class Deployment:
    version: str
    root: Path
    dlls: dict[str, Path]
    hashes: dict[str, str]


@dataclass(frozen=True, slots=True)
class LoadedDll:
    pid: int
    process_name: str
    path: Path
    architecture: str
    version: str
    sha256: str | None
    status: str


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _version_sort_key(path: Path) -> tuple[str, str, str]:
    match = VERSION_PATTERN.fullmatch(path.name)
    if match is None:
        return "", "", path.name
    return match.group(1), match.group(2), path.name


def find_latest_deployment(versions_dir: Path = VERSIONS_DIR) -> Deployment:
    if not versions_dir.is_dir():
        raise InspectionError(f"version directory does not exist: {versions_dir}")

    complete: list[tuple[Path, dict[str, Path]]] = []
    for root in versions_dir.iterdir():
        if not root.is_dir() or VERSION_PATTERN.fullmatch(root.name) is None:
            continue
        dlls = {
            architecture: root / architecture / DLL_NAME
            for architecture in ARCHITECTURES
        }
        if all(path.is_file() for path in dlls.values()):
            complete.append((root, dlls))
    if not complete:
        raise InspectionError(
            f"no complete x64/x86 deployment was found under: {versions_dir}"
        )

    root, dlls = max(complete, key=lambda candidate: _version_sort_key(candidate[0]))
    return Deployment(
        version=root.name,
        root=root,
        dlls=dlls,
        hashes={architecture: file_sha256(path) for architecture, path in dlls.items()},
    )


def deployment_version_from_path(path: Path, versions_dir: Path = VERSIONS_DIR) -> str:
    try:
        relative = path.resolve(strict=False).relative_to(versions_dir.resolve())
    except ValueError:
        return "external"
    return relative.parts[0] if len(relative.parts) >= 3 else "unknown"


def architecture_from_path(path: Path) -> str:
    architecture = path.parent.name.casefold()
    return architecture if architecture in ARCHITECTURES else "unknown"


def classify_loaded_dll(
    *,
    pid: int,
    process_name: str,
    path: Path,
    latest: Deployment,
    versions_dir: Path = VERSIONS_DIR,
) -> LoadedDll:
    try:
        digest = file_sha256(path)
    except OSError:
        digest = None
    status = (
        "latest" if digest is not None and digest in latest.hashes.values() else "old"
    )
    if digest is None:
        status = "unknown"
    return LoadedDll(
        pid=pid,
        process_name=process_name,
        path=path,
        architecture=architecture_from_path(path),
        version=deployment_version_from_path(path, versions_dir),
        sha256=digest,
        status=status,
    )


def parse_tasklist_hosts(output: str) -> list[tuple[int, str]]:
    hosts: list[tuple[int, str]] = []
    for row in csv.reader(output.splitlines()):
        if len(row) < 2 or not row[0].casefold().endswith(".exe"):
            continue
        try:
            pid = int(row[1])
        except ValueError:
            continue
        hosts.append((pid, row[0]))
    return hosts


def loaded_host_candidates() -> list[tuple[int, str]]:
    result = subprocess.run(
        ["tasklist.exe", "/m", DLL_NAME, "/fo", "csv", "/nh"],
        check=False,
        capture_output=True,
        text=True,
    )
    return parse_tasklist_hosts(result.stdout)


def iter_loaded_dlls(latest: Deployment) -> tuple[list[LoadedDll], list[str]]:
    loaded: list[LoadedDll] = []
    warnings: list[str] = []
    target = DLL_NAME.casefold()
    for pid, tasklist_name in loaded_host_candidates():
        try:
            process = psutil.Process(pid)
            paths = {
                Path(mapping.path)
                for mapping in process.memory_maps(grouped=False)
                if Path(mapping.path).name.casefold() == target
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess, OSError) as exc:
            if isinstance(exc, psutil.AccessDenied):
                warnings.append(
                    f"{tasklist_name}, PID {pid}: access denied while reading DLL path"
                )
            continue
        if not paths:
            warnings.append(
                f"{tasklist_name}, PID {pid}: tasklist reported the DLL, "
                "but its path was unavailable"
            )
            continue
        try:
            process_name = process.name()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            process_name = tasklist_name
        for path in sorted(paths, key=lambda item: os.path.normcase(os.fspath(item))):
            loaded.append(
                classify_loaded_dll(
                    pid=pid,
                    process_name=process_name,
                    path=path,
                    latest=latest,
                )
            )
    loaded.sort(key=lambda item: (item.status, item.process_name.casefold(), item.pid))
    return loaded, warnings


def _print_processes(title: str, processes: Iterable[LoadedDll]) -> None:
    names = sorted(
        {item.process_name for item in processes},
        key=str.casefold,
    )
    print(f"{title} ({len(names)}):")
    if not names:
        print("  none")
        return
    for name in names:
        print(f"  {name}")


def print_report(
    latest: Deployment, loaded: list[LoadedDll], warnings: list[str]
) -> None:
    print("Latest repository TSF DLL deployment:")
    print(f"  {latest.version}")

    print()
    _print_processes(
        "Processes using an older DLL",
        (item for item in loaded if item.status == "old"),
    )
    print()
    _print_processes(
        "Processes using the latest DLL",
        (item for item in loaded if item.status == "latest"),
    )
    unknown = [item for item in loaded if item.status == "unknown"]
    if unknown:
        print()
        _print_processes("Processes whose DLL could not be verified", unknown)
    if warnings:
        print()
        print(f"Inspection warnings ({len(warnings)}):")
        for warning in warnings:
            print(f"  {warning}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Find the latest repository TSF DLL deployment and report processes "
            "that still load an older version."
        )
    )
    parser.add_argument(
        "--fail-on-old",
        action="store_true",
        help="exit with status 2 when at least one process uses an older DLL",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if platform.system() != "Windows":
        raise InspectionError("loaded TSF DLL inspection is supported only on Windows")
    latest = find_latest_deployment()
    loaded, warnings = iter_loaded_dlls(latest)
    print_report(latest, loaded, warnings)
    if args.fail_on_old and any(item.status == "old" for item in loaded):
        return 2
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except InspectionError as exc:
        print(f"ERROR: {exc}")
        raise SystemExit(1) from exc
