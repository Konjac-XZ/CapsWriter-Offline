from __future__ import annotations

import time

import yaml

from src.infra.user_lexicon import _lexicon_path, load_words


def get_lexicon_editor_text() -> str:
    """Return the user lexicon as one editable entry per line."""
    return "\n".join(load_words())


def update_lexicon_editor_text(text: str) -> dict[str, object]:
    words = [line.strip() for line in text.splitlines() if line.strip()]
    if len(words) > 5000:
        raise ValueError("user lexicon cannot contain more than 5000 entries")
    if any(len(word) > 500 for word in words):
        raise ValueError("a user lexicon entry cannot exceed 500 characters")

    path = _lexicon_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = yaml.safe_dump(
        {"words": words},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=1000,
    )
    temporary = path.with_name(f".{path.name}.{time.time_ns()}.tmp")
    try:
        temporary.write_text(serialized, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return {"entry_count": len(words), "apply_mode": "next_recording"}
