import json
import time
import hashlib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TMP_DIR = ROOT / ".tmp"
RETRY_AUDIO_DIR = TMP_DIR / "retry_audio"
RETRY_CLAIM_DIR = TMP_DIR / "retry_claims"
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


def _atomic_write(path: Path, data: bytes) -> Path:
    tmp = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return path


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def get_latest_audio_path() -> Path | None:
    for suffix in (".mp3", ".wav"):
        path = RETRY_AUDIO_DIR / f"latest{suffix}"
        if path.exists() and path.is_file() and path.stat().st_size > 0:
            return path
    return None


def has_retry_audio() -> bool:
    return get_latest_audio_path() is not None


def write_retry_cache(
    audio_bytes: bytes,
    mime: str,
    metadata: dict[str, Any] | None = None,
    *,
    keep_source_wav: bool = False,
) -> Path:
    """Atomically replace the retry payload without doing any transcoding.

    ``keep_source_wav`` is used when ``audio_bytes`` is the already encoded
    upload payload and a fresh ``latest.wav`` was saved for playback.  The
    caller is responsible for writing that WAV first.
    """
    RETRY_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    target = latest_audio_path_for_mime(mime)
    target = _atomic_write(target, audio_bytes)

    source_wav_path: Path | None = None
    if target.suffix.lower() == ".wav":
        source_wav_path = target
        # A fresh WAV must not leave an older MP3 as the preferred retry file.
        _remove_file(latest_audio_path_for_mime("audio/mpeg"))
    elif keep_source_wav:
        candidate = latest_audio_path_for_mime("audio/wav")
        if candidate.exists() and candidate.is_file() and candidate.stat().st_size > 0:
            source_wav_path = candidate
    else:
        # Direct MP3 writes normally have no matching WAV source, so avoid
        # keeping stale playback audio next to the current retry payload.
        _remove_file(latest_audio_path_for_mime("audio/wav"))

    metadata_payload: dict[str, Any] = {
        "audio_path": str(target),
        "mime": mime_for_path(target),
        "created_at": time.time(),
    }
    if source_wav_path is not None:
        metadata_payload.update(
            {
                "source_wav_path": str(source_wav_path),
                "source_wav_mime": "audio/wav",
            }
        )
    if metadata:
        metadata_payload.update(metadata)
    tmp_meta = RETRY_METADATA_PATH.with_name(
        f".{RETRY_METADATA_PATH.name}.{time.time_ns()}.tmp"
    )
    tmp_meta.write_text(
        json.dumps(metadata_payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
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
    tmp = RETRY_REQUEST_PATH.with_name(
        f".{RETRY_REQUEST_PATH.name}.{time.time_ns()}.tmp"
    )
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(RETRY_REQUEST_PATH)
    return payload


def _retry_claim_path(request_id: Any) -> Path:
    request_key = str(request_id).encode("utf-8", errors="replace")
    claim_name = hashlib.sha256(request_key).hexdigest()
    return RETRY_CLAIM_DIR / f"{claim_name}.json"


def _prune_retry_claims(max_age_s: float = 86400.0) -> None:
    try:
        cutoff = time.time() - max_age_s
        for path in RETRY_CLAIM_DIR.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
    except Exception:
        pass


def claim_retry_request(request_id: Any) -> bool:
    """Atomically claim a retry request so only one client process handles it."""
    if request_id is None:
        return False

    RETRY_CLAIM_DIR.mkdir(parents=True, exist_ok=True)
    _prune_retry_claims()
    claim_path = _retry_claim_path(request_id)
    payload = {"request_id": request_id, "claimed_at": time.time()}

    try:
        with claim_path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return True
    except FileExistsError:
        return False
    except Exception:
        return False
