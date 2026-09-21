"""Telegram legacy Markdown formatting helpers."""

from __future__ import annotations


def md_bold(text: str) -> str:
    return f"*{text}*"


def md_italic(text: str) -> str:
    return f"_{text}_"


def md_code(text: str) -> str:
    safe = text.replace("`", "'")
    return f"`{safe}`"


def md_link(text: str, url: str) -> str:
    return f"[{text}]({url})"


def md_code_block(text: str) -> str:
    """Render multi-line output without triple-backtick fences."""
    lines = text.splitlines() or [""]
    return "\n".join(md_code(line) if line else "" for line in lines)
