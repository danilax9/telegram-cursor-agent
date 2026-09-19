"""Session title prompt and normalization for Cursor-generated titles."""

from __future__ import annotations

import re

DEFAULT_SESSION_TITLE = "Новая сессия"
_MAX_TITLE_LEN = 48
_MAX_WORDS = 3

_QUOTE_RE = re.compile(r'^["\'«»""''](.+)["\'«»""'']$')
_PUNCT_TAIL_RE = re.compile(r'[.!?:;,…\-—]+$')


def format_title_prompt(user_message: str, assistant_message: str) -> str:
    user_excerpt = user_message.strip()[:800]
    assistant_excerpt = assistant_message.strip()[:1200]
    return (
        "Create a short session title for this chat exchange.\n"
        "Rules:\n"
        "- Exactly 2-3 words\n"
        "- Same language as the user message\n"
        "- No quotes, punctuation, markdown, or explanation\n"
        "- Reply with the title only\n\n"
        f"User message:\n{user_excerpt}\n\n"
        f"Assistant reply:\n{assistant_excerpt}"
    )


def normalize_session_title(raw: str) -> str:
    """Normalize Cursor output into a short session title."""
    text = raw.strip()
    if not text:
        return ""

    text = text.splitlines()[0].strip()
    quote_match = _QUOTE_RE.match(text)
    if quote_match:
        text = quote_match.group(1).strip()
    text = _PUNCT_TAIL_RE.sub("", text).strip()
    text = re.sub(r"\s+", " ", text)

    words = text.split()
    if not words:
        return ""

    title = " ".join(words[:_MAX_WORDS])
    if len(title) <= _MAX_TITLE_LEN:
        return title
    return title[: _MAX_TITLE_LEN - 1].rsplit(" ", 1)[0] or title[:_MAX_TITLE_LEN]
