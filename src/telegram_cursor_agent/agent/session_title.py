"""Generate short 2-3 word session titles."""

from __future__ import annotations

import re

_MAX_TITLE_LEN = 48
_MAX_WORDS = 3
_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MARKDOWN_EMPHASIS_RE = re.compile(r"[*_#]+")
_TOKEN_RE = re.compile(r"[/\w.-]+", re.UNICODE)

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

_STOP_WORDS = frozenset({
    "a",
    "an",
    "the",
    "to",
    "for",
    "and",
    "or",
    "in",
    "on",
    "at",
    "is",
    "are",
    "me",
    "my",
    "you",
    "your",
    "i",
    "we",
    "it",
    "this",
    "that",
    "with",
    "from",
    "как",
    "что",
    "это",
    "для",
    "при",
    "или",
    "ещё",
    "еще",
    "ли",
    "бы",
    "на",
    "в",
    "и",
    "по",
    "из",
    "не",
    "да",
    "нет",
    "мне",
    "меня",
    "тебе",
    "нам",
    "добавить",
    "добавь",
    "исправить",
    "исправь",
    "сделать",
    "сделай",
    "создать",
    "создай",
    "реализовать",
    "реализуй",
    "нужно",
    "хочу",
    "можно",
    "please",
    "help",
    "make",
    "add",
    "fix",
    "create",
    "implement",
    "use",
    "using",
    "command",
    "команда",
    "команды",
    "команду",
})


def generate_session_title(user_message: str, assistant_message: str = "") -> str:
    """Return a 2-3 word title derived mainly from the user's first message."""
    words = _pick_title_words(user_message)
    if len(words) < 2 and assistant_message:
        for token in _pick_title_words(assistant_message):
            if token.lower() not in {word.lower() for word in words}:
                words.append(token)
            if len(words) >= _MAX_WORDS:
                break

    if not words:
        return "Новая сессия"

    title = " ".join(words[:_MAX_WORDS])
    if len(title) <= _MAX_TITLE_LEN:
        return title
    return title[: _MAX_TITLE_LEN - 1].rsplit(" ", 1)[0] or title[:_MAX_TITLE_LEN]


def _pick_title_words(message: str) -> list[str]:
    text = _normalize_text(message)
    if not text:
        return []

    lowered = text.lower()
    for prefix in _FILLER_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :].strip()
            lowered = text.lower()
            break

    words: list[str] = []
    for token in _TOKEN_RE.findall(text):
        cleaned = token.strip("-_.")
        if not cleaned or cleaned.lower() in _STOP_WORDS:
            continue
        if len(cleaned) == 1 and not cleaned.startswith("/"):
            continue
        words.append(cleaned)
        if len(words) >= _MAX_WORDS:
            break
    return words


def _normalize_text(text: str) -> str:
    cleaned = _CODE_BLOCK_RE.sub(" ", text)
    cleaned = _INLINE_CODE_RE.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_LINK_RE.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_EMPHASIS_RE.sub(" ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned
