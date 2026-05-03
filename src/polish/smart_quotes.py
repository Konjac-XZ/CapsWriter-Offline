from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


_QUOTE_PATTERN = re.compile(r"""["']""")
_CJK_PATTERN = re.compile(r"[\u3400-\u9fff]")
_FRONTMATTER_PATTERN = re.compile(r"\A(---|\+\+\+)[^\n]*\n.*?\n\1[ \t]*(?:\n|$)", re.DOTALL)
_FENCED_CODE_PATTERN = re.compile(r"(?ms)^([ \t]*)(`{3,}|~{3,}).*?\n.*?^\1\2[ \t]*$")
_INDENTED_CODE_PATTERN = re.compile(r"(?m)(?:^(?: {4}|\t).*(?:\n|$))+")
_HTML_BLOCK_PATTERN = re.compile(
    r"(?is)<(script|style|pre|code|textarea|[^!/?\s>]+)\b[^>]*>.*?</\1>"
)
_HTML_COMMENT_PATTERN = re.compile(r"(?s)<!--.*?-->")
_HTML_TAG_PATTERN = re.compile(r"(?s)</?[^>\n]+>")
_MATH_BLOCK_PATTERN = re.compile(r"(?s)\$\$.*?\$\$")
_INLINE_MATH_PATTERN = re.compile(r"(?<!\\)\$(?!\s)(?:\\.|[^$\\\n])+(?<!\s)(?<!\\)\$")
_INLINE_CODE_PATTERN = re.compile(r"(`+)(?:.|\n)*?\1")
_AUTOLINK_PATTERN = re.compile(r"<(?:https?|ftp|mailto):[^<>\s]+>", re.IGNORECASE)
_BARE_URL_PATTERN = re.compile(
    r"(?i)\b(?:https?|ftp)://[^\s<>()\[\]{}\"'，。！？；：、]+"
)
_EMAIL_PATTERN = re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b")
_WINDOWS_PATH_PATTERN = re.compile(
    r"(?i)(?<![\w/\\])(?:[A-Z]:\\|\\\\)[^\s<>，。！？；：、]+"
)
_UNIX_PATH_PATTERN = re.compile(
    r"(?<![\w.])(?:\.{1,2}/|/)[^\s<>，。！？；：、]+"
)
_LINK_DEST_PATTERN = re.compile(r"(?<=\]\()[^)\n]+(?=\))")

_URLISH_PATTERN = re.compile(r"(?i)(?:https?://|ftp://|www\.|mailto:)")
_PATHISH_PATTERN = re.compile(r"(?i)(?:[A-Z]:\\|\\\\|(?:^|[\s(])(?:\.{1,2}/|/)\S+)")
_JSON_LIKE_PATTERN = re.compile(r"^\s*[\[{].*[\]}]\s*$", re.DOTALL)
_YAML_LINE_PATTERN = re.compile(r"^\s*[A-Za-z0-9_.-]+\s*:\s+.+$")

_OPEN_PUNCT = set("([{<（［｛《〈「『【〔〖“‘")
_CLOSE_PUNCT = set(")]}>）］｝》〉」』】〕〗”’")
_END_PUNCT = set(".,;:!?，。；：！？、…")
_CLOSING_AFTER_QUOTE = _CLOSE_PUNCT | _END_PUNCT


@dataclass
class _QuoteState:
    double_depth: int = 0
    single_depth: int = 0


def normalize_zh_cn_smart_quotes(text: str) -> str:
    """Normalize straight quotes in Chinese Markdown-ish prose.

    The processor is intentionally conservative. It protects Markdown/code-like
    structures first, then transforms only prose chunks that contain CJK text.
    """

    if not text or not _QUOTE_PATTERN.search(text):
        return text

    protected, restore = _protect_non_prose_ranges(text)
    state = _QuoteState()
    normalized = _normalize_prose_chunks(protected, state)
    return restore(normalized)


def _protect_non_prose_ranges(text: str) -> tuple[str, Callable[[str], str]]:
    placeholders: list[str] = []

    def protect_match(match: re.Match[str]) -> str:
        placeholders.append(match.group(0))
        return f"\x00SQ{len(placeholders) - 1}\x00"

    protected = text
    frontmatter = _FRONTMATTER_PATTERN.match(protected)
    if frontmatter:
        raw = frontmatter.group(0)
        placeholders.append(raw)
        protected = f"\x00SQ{len(placeholders) - 1}\x00" + protected[len(raw):]

    for pattern in (
        _FENCED_CODE_PATTERN,
        _INDENTED_CODE_PATTERN,
        _HTML_COMMENT_PATTERN,
        _HTML_BLOCK_PATTERN,
        _MATH_BLOCK_PATTERN,
        _INLINE_CODE_PATTERN,
        _INLINE_MATH_PATTERN,
        _AUTOLINK_PATTERN,
        _LINK_DEST_PATTERN,
        _BARE_URL_PATTERN,
        _EMAIL_PATTERN,
        _WINDOWS_PATH_PATTERN,
        _UNIX_PATH_PATTERN,
        _HTML_TAG_PATTERN,
    ):
        protected = pattern.sub(protect_match, protected)

    def restore(value: str) -> str:
        for index, raw in enumerate(placeholders):
            value = value.replace(f"\x00SQ{index}\x00", raw)
        return value

    return protected, restore


def _normalize_prose_chunks(text: str, state: _QuoteState) -> str:
    parts = re.split(r"(\n\s*\n)", text)
    return "".join(
        _normalize_chunk(part, state) if not re.fullmatch(r"\n\s*\n", part) else part
        for part in parts
    )


def _normalize_chunk(chunk: str, state: _QuoteState) -> str:
    if not _QUOTE_PATTERN.search(chunk):
        return chunk
    if not _contains_cjk(chunk):
        return chunk
    if _looks_structured(chunk):
        return chunk

    result: list[str] = []
    for index, char in enumerate(chunk):
        if char == '"':
            result.append(_normalize_double_quote(chunk, index, state))
        elif char == "'":
            result.append(_normalize_single_quote(chunk, index, state))
        else:
            result.append(char)
    return "".join(result)


def _normalize_double_quote(text: str, index: int, state: _QuoteState) -> str:
    prev_char = _previous_visible_char(text, index)
    next_char = _next_visible_char(text, index)

    if _is_measure_quote(prev_char, next_char):
        return "″"

    if _is_open_quote_context(prev_char, next_char, state.double_depth):
        state.double_depth += 1
        return "“"

    if state.double_depth > 0:
        state.double_depth -= 1
    return "”"


def _normalize_single_quote(text: str, index: int, state: _QuoteState) -> str:
    prev_char = _previous_visible_char(text, index)
    next_char = _next_visible_char(text, index)

    if _is_measure_quote(prev_char, next_char):
        return "′"

    if _is_english_apostrophe(prev_char, next_char):
        return "’"

    if _is_open_quote_context(prev_char, next_char, state.single_depth):
        state.single_depth += 1
        return "‘"

    if state.single_depth > 0:
        state.single_depth -= 1
    return "’"


def _is_measure_quote(prev_char: str | None, next_char: str | None) -> bool:
    if not prev_char or not prev_char.isdigit():
        return False
    return (
        next_char is None
        or next_char.isdigit()
        or next_char.isspace()
        or next_char in _CLOSING_AFTER_QUOTE
    )


def _is_english_apostrophe(prev_char: str | None, next_char: str | None) -> bool:
    if prev_char and next_char and _is_ascii_alpha(prev_char) and _is_ascii_alpha(next_char):
        return True
    if prev_char and _is_ascii_alpha(prev_char) and (
        next_char is None or next_char.isspace() or next_char in _CLOSING_AFTER_QUOTE
    ):
        return True
    if next_char and next_char.isdigit() and (
        prev_char is None or prev_char.isspace() or prev_char in _OPEN_PUNCT
    ):
        return True
    return False


def _is_open_quote_context(
    prev_char: str | None,
    next_char: str | None,
    depth: int,
) -> bool:
    prev_says_open = prev_char is None or prev_char.isspace() or prev_char in _OPEN_PUNCT
    next_says_close = next_char is None or next_char.isspace() or next_char in _CLOSING_AFTER_QUOTE
    if prev_says_open and not next_says_close:
        return True
    if next_says_close and not prev_says_open:
        return False
    return depth == 0


def _previous_visible_char(text: str, index: int) -> str | None:
    pos = index - 1
    while pos >= 0:
        char = text[pos]
        if not _is_markdown_syntax_char(char):
            return char
        pos -= 1
    return None


def _next_visible_char(text: str, index: int) -> str | None:
    pos = index + 1
    while pos < len(text):
        char = text[pos]
        if not _is_markdown_syntax_char(char):
            return char
        pos += 1
    return None


def _is_markdown_syntax_char(char: str) -> bool:
    return char in "*_~"


def _is_ascii_alpha(char: str) -> bool:
    return ("a" <= char <= "z") or ("A" <= char <= "Z")


def _contains_cjk(text: str) -> bool:
    return bool(_CJK_PATTERN.search(text))


def _looks_structured(chunk: str) -> bool:
    stripped = chunk.strip()
    if not stripped:
        return False

    if _JSON_LIKE_PATTERN.match(stripped):
        return True
    if _PATHISH_PATTERN.search(stripped) and not _contains_cjk(stripped):
        return True
    if _looks_url_dominant(stripped):
        return True

    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    if len(lines) >= 2 and sum(bool(_YAML_LINE_PATTERN.match(line)) for line in lines) >= 2:
        return True
    if len(lines) == 1:
        line = lines[0]
        if line.startswith(("$ ", "> ", ">>> ", "PS ", "PS> ")):
            return True
        if _YAML_LINE_PATTERN.match(line) and not _contains_cjk(line):
            return True

    return False


def _looks_url_dominant(text: str) -> bool:
    if not _URLISH_PATTERN.search(text):
        return False
    non_space_len = len(re.sub(r"\s+", "", text))
    if non_space_len == 0:
        return False
    url_len = sum(len(match.group(0)) for match in _BARE_URL_PATTERN.finditer(text))
    return url_len / non_space_len >= 0.5 and not _contains_cjk(text)
