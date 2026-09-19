"""Generate concise session titles from the first user/agent exchange."""

from __future__ import annotations

import re

_MAX_TITLE_LEN = 64
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MARKDOWN_EMPHASIS_RE = re.compile(r"[*_#]+")
_WHITESPACE_RE = re.compile(r"\s+")
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")

_FILLER_PREFIXES = (
    "можешь ли ",
    "можешь ",
    "please ",
    "could you ",
    "can you ",
    "help me ",
    "нужно ",
    "хочу ",
    "давай ",
    "сделай ",
    "сделайте ",
    "добавь ",
    "добавьте ",
    "исправь ",
    "исправьте ",
    "реализуй ",
    "реализуйте ",
    "создай ",
    "создайте ",
)

_GENERIC_ASSISTANT_STARTS = (
    "готово",
    "done",
    "sure",
    "ok",
    "okay",
    "yes",
    "конечно",
    "хорошо",
    "понял",
    "understood",
)


def generate_session_title(user_message: str, assistant_message: str) -> str:
    """Build a short title summarizing the opening exchange."""
    user_summary = _summarize_user_intent(user_message)
    assistant_summary = _summarize_assistant_reply(assistant_message)

    if user_summary and assistant_summary:
        combined = f"{user_summary} — {assistant_summary}"
        if len(combined) <= _MAX_TITLE_LEN:
            return combined
        if len(user_summary) <= 40:
            remaining = _MAX_TITLE_LEN - len(user_summary) - 3
            if remaining > 12:
                return f"{user_summary} — {_truncate(assistant_summary, remaining)}"
        return _truncate(user_summary, _MAX_TITLE_LEN)

    return _truncate(user_summary or assistant_summary or "Новая сессия", _MAX_TITLE_LEN)


def _summarize_user_intent(message: str) -> str:
    text = _normalize_text(message)
    if not text:
        return ""

    lowered = text.lower()
    for prefix in _FILLER_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :].strip()
            lowered = text.lower()
            break

    sentence = _first_sentence(text)
    sentence = sentence.rstrip("?.!…")
    return _truncate(sentence, 42)


def _summarize_assistant_reply(message: str) -> str:
    text = _normalize_text(message)
    if not text:
        return ""

    for line in text.splitlines():
        heading = _extract_markdown_heading(line)
        if heading:
            return _truncate(heading, 32)

    for sentence in _split_sentences(text):
        cleaned = sentence.strip()
        if len(cleaned) < 8:
            continue
        if cleaned.lower() in _GENERIC_ASSISTANT_STARTS:
            continue
        if cleaned.lower().startswith(_GENERIC_ASSISTANT_STARTS):
            continue
        return _truncate(cleaned.rstrip("?.!…"), 32)

    return _truncate(text, 32)


def _normalize_text(text: str) -> str:
    cleaned = _CODE_BLOCK_RE.sub(" ", text)
    cleaned = _INLINE_CODE_RE.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_EMPHASIS_RE.sub("", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned).strip()
    return cleaned


def _first_sentence(text: str) -> str:
    parts = _split_sentences(text)
    return parts[0] if parts else text


def _split_sentences(text: str) -> list[str]:
    parts = [part.strip() for part in _SENTENCE_SPLIT_RE.split(text) if part.strip()]
    return parts or [text.strip()]


def _extract_markdown_heading(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("#"):
        return None
    return _MARKDOWN_EMPHASIS_RE.sub("", stripped.lstrip("#")).strip() or None


def _truncate(text: str, max_len: int) -> str:
    text = text.strip()
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rsplit(" ", 1)[0].strip()
    if not cut:
        cut = text[: max_len - 1]
    return f"{cut}…"
