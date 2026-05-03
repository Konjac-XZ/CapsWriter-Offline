from __future__ import annotations

import os
import re
import sys
from pathlib import Path


def _get_root_dir() -> Path:
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys.executable).resolve().parent
    # src/infra/ → src/ → project root
    return Path(__file__).resolve().parent.parent.parent


def _load_with_python_dotenv(root: Path) -> bool:
    try:
        from dotenv import load_dotenv
    except Exception:
        return False

    loaded = False
    for path in (root / ".env", root / ".env.local"):
        if not path.exists():
            continue
        load_dotenv(str(path), override=True)
        loaded = True
    return loaded


def _load_manually(root: Path) -> dict[str, str]:
    loaded: dict[str, str] = {}
    for path in (root / ".env", root / ".env.local"):
        if not path.exists():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            text = path.read_text()

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, val = line.split("=", 1)
            key = key.strip()
            val = val.strip()

            if (val.startswith('"') and val.endswith('"')) or (
                val.startswith("'") and val.endswith("'")
            ):
                val = val[1:-1]
            elif "#" in val:
                val = val.split("#", 1)[0].strip()

            def _replace_var(m: re.Match[str]) -> str:
                name = m.group(1) or m.group(2)
                return os.environ.get(name, "")

            val = re.sub(r"\$(?:{([^}]+)}|([A-Za-z_][A-Za-z0-9_]*))", _replace_var, val)
            os.environ[key] = val
            loaded[key] = val

    return loaded


def load_dotenv_files() -> dict[str, str]:
    """Load .env and .env.local from the project root into ``os.environ``."""
    root = _get_root_dir()
    found_files: list[str] = [
        str(p) for p in (root / ".env", root / ".env.local") if p.exists()
    ]
    if not found_files:
        print("[env_loader] 未找到 .env / .env.local 文件，环境变量未从文件加载。")
        return {}

    if _load_with_python_dotenv(root):
        loaded: dict[str, str] = {}
        for path in (root / ".env", root / ".env.local"):
            if not path.exists():
                continue
            try:
                for raw_line in path.read_text(encoding="utf-8").splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key = line.split("=", 1)[0].strip()
                    if key:
                        loaded[key] = os.environ.get(key, "")
            except Exception:
                continue
        # _log_llm_keys(loaded)
        return loaded

    loaded = _load_manually(root)
    # _log_llm_keys(loaded)
    return loaded


def _log_llm_keys(loaded: dict[str, str]) -> None:
    _log_provider_keys(
        loaded,
        prefix="LLM_POLISH",
        label="LLM 润色",
        config_path="config/polish/polish.yaml",
    )
    _log_provider_keys(
        loaded,
        prefix="LLM_VISION",
        label="视觉上下文",
        config_path="config/polish/vision.yaml",
    )


def _log_provider_keys(
    loaded: dict[str, str],
    *,
    prefix: str,
    label: str,
    config_path: str,
) -> None:
    # Only the credentials remain in .env; all other settings live in config/polish/polish.yaml
    cred_keys = [k for k in (f"{prefix}_BASE_URL", f"{prefix}_API_KEY") if k in loaded]
    if cred_keys:
        # Mask the API key value
        def _mask(k: str) -> str:
            v = loaded[k]
            if k.endswith("_API_KEY") and len(v) > 6:
                return v[:4] + "****" + v[-2:]
            return v
        pairs = ", ".join(f"{k}={_mask(k)}" for k in cred_keys)
        print(f"[env_loader] {label}凭据：{pairs}（其他配置见 {config_path}）")
    else:
        print(f"[env_loader] 未检测到 {prefix}_BASE_URL / {prefix}_API_KEY（{label}功能不可用）。")
