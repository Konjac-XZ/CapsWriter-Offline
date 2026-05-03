import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TMP_DIR = ROOT / ".tmp"
RETRY_AUDIO_DIR = TMP_DIR / "retry_audio"
RETRY_REQUEST_PATH = TMP_DIR / "retry_latest_request.json"
RETRY_METADATA_PATH = RETRY_AUDIO_DIR / "latest.json"

_MIME_TO_SUFFIX = {
    "audio/mpeg": ".mp3",
    "audio/mp3": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/wave": ".wav",
}

_SUFFIX_TO_MIME = {
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
}


def suffix_for_mime(mime: str) -> str:
    return _MIME_TO_SUFFIX.get((mime or "").lower(), ".wav")


def mime_for_path(path: Path) -> str:
    return _SUFFIX_TO_MIME.get(path.suffix.lower(), "audio/wav")


def latest_audio_path_for_mime(mime: str) -> Path:
    return RETRY_AUDIO_DIR / f"latest{suffix_for_mime(mime)}"


def get_latest_audio_path() -> Path | None:
    for suffix in (".mp3", ".wav"):
        path = RETRY_AUDIO_DIR / f"latest{suffix}"
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def has_retry_audio() -> bool:
    return get_latest_audio_path() is not None


def write_retry_cache(audio_bytes: bytes, mime: str, metadata: dict[str, Any] | None = None) -> Path:
    RETRY_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    target = latest_audio_path_for_mime(mime)
    tmp = target.with_name(f".{target.name}.{time.time_ns()}.tmp")
    tmp.write_bytes(audio_bytes)
    tmp.replace(target)

    # Keep exactly one latest audio file even if the payload format changes.
    for suffix in (".mp3", ".wav"):
        other = RETRY_AUDIO_DIR / f"latest{suffix}"
        if other != target:
            try:
                other.unlink()
            except FileNotFoundError:
                pass

    metadata_payload = {
        "audio_path": str(target),
        "mime": mime,
        "created_at": time.time(),
    }
    if metadata:
        metadata_payload.update(metadata)
    tmp_meta = RETRY_METADATA_PATH.with_name(f".{RETRY_METADATA_PATH.name}.{time.time_ns()}.tmp")
    tmp_meta.write_text(json.dumps(metadata_payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_meta.replace(RETRY_METADATA_PATH)
    return target


def read_retry_request() -> dict[str, Any] | None:
    try:
        return json.loads(RETRY_REQUEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None


def write_retry_request() -> dict[str, Any]:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"request_id": time.time_ns(), "created_at": time.time()}
    tmp = RETRY_REQUEST_PATH.with_name(f".{RETRY_REQUEST_PATH.name}.{time.time_ns()}.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(RETRY_REQUEST_PATH)
    return payload
