import json
from typing import Any, Optional


# Known noisy, non-content prefixes some providers or local logs emit
_NOISE_PREFIXES = (
    "正在聆听",  # status
    "录音时长",  # local duration log
    "持久连接已建立",  # local connection log
    "持久连接已关闭",  # local connection log
    "流式转录",  # local streaming flag
    "转录时延",  # local latency log
)


def looks_like_noise(line: str) -> bool:
    s = (line or "").strip()
    if not s:
        return True
    for p in _NOISE_PREFIXES:
        if s.startswith(p):
            return True
    return False


def _extract_text_from_obj(obj: Any) -> Optional[str]:
    """Try to extract transcript text from a JSON-like object."""
    try:
        if isinstance(obj, str):
            return obj
        if not isinstance(obj, dict):
            return None

        # Common OpenAI-style response for transcriptions
        if isinstance(obj.get("text"), str):
            return obj["text"]

        # Fallbacks seen across providers
        # choices[0].delta.content (SSE-like)
        try:
            delta = obj["choices"][0]["delta"].get("content")
            if isinstance(delta, str):
                return delta
        except Exception:
            pass

        # choices[0].text (non-chat completions)
        try:
            ch_text = obj["choices"][0].get("text")
            if isinstance(ch_text, str):
                return ch_text
        except Exception:
            pass

        # message.content (chat-style full responses)
        try:
            content = obj["message"].get("content")
            if isinstance(content, str):
                return content
        except Exception:
            pass

        return None
    except Exception:
        return None


def _try_json_load(s: str) -> Optional[Any]:
    try:
        return json.loads(s)
    except Exception:
        return None


def extract_json_from_labeled_line(line: str) -> Optional[Any]:
    """Handle lines like "识别结果：{...json...}" and return the JSON object if found."""
    if not line:
        return None
    s = line.strip()
    # Only handle when a JSON object appears in the line
    lb = s.find("{")
    rb = s.rfind("}")
    if lb == -1 or rb == -1 or rb <= lb:
        return None
    return _try_json_load(s[lb : rb + 1])


def extract_text_from_body(body: str) -> Optional[str]:
    """Extract only the transcript text from a response body that may be:
    - Plain text
    - A JSON object (possibly with usage fields)
    - A multi-line log with a final labeled JSON line (e.g., "识别结果：{...}")
    Returns the extracted text or None if no clear transcript was found.
    """
    if not body:
        return None

    s = body.strip()

    # 1) Try whole-body JSON first
    obj = _try_json_load(s)
    txt = _extract_text_from_obj(obj) if obj is not None else None
    if isinstance(txt, str) and txt.strip() != "":
        return txt

    # 2) Scan line by line for the last usable text
    last_text: Optional[str] = None
    for line in s.splitlines():
        line = line.strip()
        if not line:
            continue

        # Try strict JSON per line
        obj = _try_json_load(line)
        txt = _extract_text_from_obj(obj) if obj is not None else None
        if isinstance(txt, str) and txt.strip() != "":
            last_text = txt
            continue

        # Try labeled line with inline JSON
        obj = extract_json_from_labeled_line(line)
        txt = _extract_text_from_obj(obj) if obj is not None else None
        if isinstance(txt, str) and txt.strip() != "":
            last_text = txt
            continue

        # Keep last non-noise, non-control line as a weak fallback
        if not looks_like_noise(line):
            last_text = line

    return last_text

