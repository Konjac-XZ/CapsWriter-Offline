"""Qwen Audio Legacy response parsing helpers."""

import json
from typing import Any, Dict, List, Tuple


def _extract_text_from_content(content: Any) -> List[str]:
    parts: List[str] = []
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict):
                if "text" in item and isinstance(item["text"], str):
                    parts.append(item["text"])
                elif "content" in item:
                    parts.extend(_extract_text_from_content(item["content"]))
            elif isinstance(item, str):
                parts.append(item)
            else:
                parts.extend(_extract_text_from_content(item))
    elif isinstance(content, dict):
        parts.extend(_extract_text_from_content(content.get("content")))
        if "text" in content and isinstance(content["text"], str):
            parts.append(content["text"])
    elif isinstance(content, str):
        parts.append(content)
    elif content is not None:
        text = getattr(content, "text", None)
        if isinstance(text, str):
            parts.append(text)
        nested = getattr(content, "content", None)
        if nested is not None:
            parts.extend(_extract_text_from_content(nested))
    return parts


def _normalize_text(s: str) -> str:
    if not isinstance(s, str):
        return ""
    s = s.strip()
    if len(s) >= 2 and (
        (s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")
    ):
        s = s[1:-1]
    return " ".join(s.split())


def _get_field(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _extract_parts_from_choice(choice: Any) -> List[str]:
    parts: List[str] = []
    parts.extend(_extract_text_from_content(_get_field(choice, "content")))
    text = _get_field(choice, "text")
    if isinstance(text, str):
        parts.append(text)
    message = _get_field(choice, "message")
    if message is not None:
        parts.extend(_extract_text_from_content(_get_field(message, "content")))
    return parts


def payload_preview(payload: Any) -> str | None:
    try:
        preview = json.dumps(payload, ensure_ascii=False, default=str)
    except Exception:
        try:
            preview = str(payload)
        except Exception:
            return None
    preview = " ".join(preview.split())
    if len(preview) > 500:
        preview = preview[:500] + "..."
    return preview or None


def sdk_response_preview(response: Any, payload_data: Any) -> str | None:
    parts: List[str] = []
    try:
        parts.append(f"response_type={type(response).__name__}")
    except Exception:
        pass
    for name in ("status_code", "code", "message", "request_id", "output"):
        try:
            value = getattr(response, name, None)
        except Exception:
            value = None
        if value is not None:
            parts.append(f"{name}={payload_preview(value) or value}")
    payload = payload_preview(payload_data)
    if payload:
        parts.append(f"payload={payload}")
    preview = " ".join(str(part) for part in parts if part)
    if len(preview) > 900:
        preview = preview[:900] + "..."
    return preview or None


def extract_transcript(payload: Any) -> Tuple[str, Dict[str, Any]]:
    request_id = None
    message = None
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
            return extract_transcript(parsed)
        except Exception:
            text = _normalize_text(payload)
            return text, {}
    if isinstance(payload, dict):
        request_id = payload.get("request_id") or payload.get("id")
        message = payload.get("message")
        output = payload.get("output") or payload.get("result") or payload.get("data")
        parts: List[str] = []
        if isinstance(output, dict):
            results = output.get("results") or output.get("choices")
            if isinstance(results, list):
                for item in results:
                    parts.extend(_extract_parts_from_choice(item))
            else:
                parts.extend(_extract_text_from_content(output))
        elif isinstance(output, list):
            for item in output:
                parts.extend(_extract_text_from_content(item))
                parts.extend(_extract_parts_from_choice(item))
        results = payload.get("results") or payload.get("choices")
        if isinstance(results, list):
            for item in results:
                parts.extend(_extract_parts_from_choice(item))
        if not parts and "text" in payload and isinstance(payload["text"], str):
            parts = [payload["text"]]
        text = "\n".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
        if text:
            text = _normalize_text(text)
    else:
        text = ""

    meta = {"qwen_audio_legacy_request_id": request_id}
    if message:
        meta["qwen_audio_legacy_message"] = message
    if not text:
        preview = payload_preview(payload)
        if preview:
            meta["qwen_audio_legacy_payload_preview"] = preview
    return text, meta
