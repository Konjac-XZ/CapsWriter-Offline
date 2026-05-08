import hashlib
import json
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
TMP_DIR = ROOT / ".tmp"
ABANDON_CLAIM_DIR = TMP_DIR / "abandon_claims"
ABANDON_REQUEST_PATH = TMP_DIR / "abandon_current_request.json"
CLEAR_HISTORY_CLAIM_DIR = TMP_DIR / "clear_history_claims"
CLEAR_HISTORY_REQUEST_PATH = TMP_DIR / "clear_finalized_history_request.json"


def read_abandon_request() -> dict[str, Any] | None:
    try:
        return json.loads(ABANDON_REQUEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None


def write_abandon_request() -> dict[str, Any]:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"request_id": time.time_ns(), "created_at": time.time()}
    tmp = ABANDON_REQUEST_PATH.with_name(
        f".{ABANDON_REQUEST_PATH.name}.{time.time_ns()}.tmp"
    )
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(ABANDON_REQUEST_PATH)
    return payload


def read_clear_history_request() -> dict[str, Any] | None:
    try:
        return json.loads(CLEAR_HISTORY_REQUEST_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception:
        return None


def write_clear_history_request() -> dict[str, Any]:
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    payload = {"request_id": time.time_ns(), "created_at": time.time()}
    tmp = CLEAR_HISTORY_REQUEST_PATH.with_name(
        f".{CLEAR_HISTORY_REQUEST_PATH.name}.{time.time_ns()}.tmp"
    )
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CLEAR_HISTORY_REQUEST_PATH)
    return payload


def _abandon_claim_path(request_id: Any) -> Path:
    request_key = str(request_id).encode("utf-8", errors="replace")
    claim_name = hashlib.sha256(request_key).hexdigest()
    return ABANDON_CLAIM_DIR / f"{claim_name}.json"


def _prune_abandon_claims(max_age_s: float = 86400.0) -> None:
    try:
        cutoff = time.time() - max_age_s
        for path in ABANDON_CLAIM_DIR.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
    except Exception:
        pass


def claim_abandon_request(request_id: Any) -> bool:
    if request_id is None:
        return False

    ABANDON_CLAIM_DIR.mkdir(parents=True, exist_ok=True)
    _prune_abandon_claims()
    claim_path = _abandon_claim_path(request_id)
    payload = {"request_id": request_id, "claimed_at": time.time()}

    try:
        with claim_path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return True
    except FileExistsError:
        return False
    except Exception:
        return False


def _clear_history_claim_path(request_id: Any) -> Path:
    request_key = str(request_id).encode("utf-8", errors="replace")
    claim_name = hashlib.sha256(request_key).hexdigest()
    return CLEAR_HISTORY_CLAIM_DIR / f"{claim_name}.json"


def _prune_clear_history_claims(max_age_s: float = 86400.0) -> None:
    try:
        cutoff = time.time() - max_age_s
        for path in CLEAR_HISTORY_CLAIM_DIR.glob("*.json"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
            except FileNotFoundError:
                pass
            except Exception:
                pass
    except Exception:
        pass


def claim_clear_history_request(request_id: Any) -> bool:
    if request_id is None:
        return False

    CLEAR_HISTORY_CLAIM_DIR.mkdir(parents=True, exist_ok=True)
    _prune_clear_history_claims()
    claim_path = _clear_history_claim_path(request_id)
    payload = {"request_id": request_id, "claimed_at": time.time()}

    try:
        with claim_path.open("x", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
        return True
    except FileExistsError:
        return False
    except Exception:
        return False
