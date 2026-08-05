"""Preview or migrate legacy transcription provider YAML files to schema v2."""

from __future__ import annotations

import argparse
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml


def migrate_provider_data(data: dict[str, Any]) -> dict[str, Any]:
    """Return a schema-v2 copy; already migrated files are returned unchanged."""
    if int(data.get("schema_version", 1)) >= 2 and isinstance(data.get("models"), dict):
        return dict(data)

    migrated = dict(data)
    shared = dict(data.get("settings") or {})
    upstream_model = str(shared.pop("model", "default"))
    realtime_present = "realtime" in shared
    realtime_enabled = bool(shared.pop("realtime", False))
    realtime_model = str(shared.pop("realtime_model", upstream_model))
    stream = bool(shared.get("stream", False))

    modes: dict[str, Any] = {
        "file_upload": {"settings": {"model": upstream_model, "realtime": False}}
    }
    if realtime_present:
        modes["live_audio"] = {
            "settings": {"model": realtime_model, "realtime": True}
        }

    migrated["schema_version"] = 2
    migrated["settings"] = shared
    migrated["models"] = {
        "default": {
            "name": upstream_model,
            "upstream_model": upstream_model,
            "incremental_output": stream,
            "default_mode": "live_audio" if realtime_enabled else "file_upload",
            "modes": modes,
        }
    }
    return migrated


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as handle:
        yaml.safe_dump(data, handle, allow_unicode=True, sort_keys=False)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def migrate_directory(config_dir: Path, *, write: bool = False) -> list[Path]:
    changed: list[Path] = []
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for path in sorted(config_dir.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        if not isinstance(raw, dict):
            raise ValueError(f"Provider config is not a mapping: {path}")
        migrated = migrate_provider_data(raw)
        if migrated == raw:
            continue
        changed.append(path)
        if write:
            backup = path.with_suffix(f"{path.suffix}.{timestamp}.bak")
            shutil.copy2(path, backup)
            _atomic_write(path, migrated)
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config-dir",
        type=Path,
        default=Path(__file__).parents[2] / "config" / "providers",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Create timestamped backups and atomically write schema-v2 files.",
    )
    args = parser.parse_args()
    changed = migrate_directory(args.config_dir, write=args.write)
    action = "Migrated" if args.write else "Would migrate"
    for path in changed:
        print(f"{action}: {path}")
    print(f"{len(changed)} provider file(s) {'changed' if args.write else 'need migration'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
