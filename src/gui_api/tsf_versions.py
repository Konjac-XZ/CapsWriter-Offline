"""On-demand adapter for the existing TSF loaded-DLL inspection tooling."""

from __future__ import annotations

from native.tsf_speech_tip.check_loaded_versions import (
    find_latest_deployment,
    iter_loaded_dlls,
)


def inspect_tsf_dll_versions() -> dict[str, object]:
    latest = find_latest_deployment()
    loaded, warnings = iter_loaded_dlls(latest)
    hosts: list[dict[str, str]] = []
    seen_hosts: set[tuple[str, str]] = set()
    for item in loaded:
        key = item.process_name.casefold(), item.status
        if key in seen_hosts:
            continue
        seen_hosts.add(key)
        hosts.append({"process_name": item.process_name, "status": item.status})
    return {
        "latest": {
            "version": latest.version,
        },
        "hosts": hosts,
        "warnings": warnings,
        "latest_count": sum(item["status"] == "latest" for item in hosts),
        "old_count": sum(item["status"] == "old" for item in hosts),
        "unknown_count": sum(item["status"] == "unknown" for item in hosts),
    }
