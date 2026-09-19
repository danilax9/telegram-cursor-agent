"""Authorization and output redaction."""

import html
import re
from collections.abc import Sequence

from telegram_cursor_agent.core.config import Settings

_CODE_BLOCK_RE = re.compile(r"```(?:[^\n]*)\n(.*?)```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")
_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC_RE = re.compile(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)")
_HEADER_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]+"),
]


def is_admin(telegram_user_id: int, settings: Settings) -> bool:
    return telegram_user_id in settings.telegram_admin_ids


def require_admin(telegram_user_id: int, settings: Settings) -> None:
    if not is_admin(telegram_user_id, settings):
        raise PermissionError(f"User {telegram_user_id} is not authorized")


def redact_secrets(text: str) -> str:
    result = text
    for pattern in _SECRET_PATTERNS:
        result = pattern.sub(lambda m: _redact_match(m.group(0)), result)
    return result


def _redact_match(value: str) -> str:
    if "=" in value:
        key, _ = value.split("=", 1)
        return f"{key}=[REDACTED]"
    if ":" in value:
        key, _ = value.split(":", 1)
        return f"{key}:[REDACTED]"
    return "[REDACTED]"


def truncate_output(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    return f"{truncated}\n\n[... output truncated at {max_bytes} bytes ...]"


def markdown_to_telegram_html(text: str) -> str:
    """Convert common Markdown from Cursor into Telegram HTML."""
    if not text:
        return text
    if _looks_like_telegram_html(text):
        return text

    parts: list[str] = []
    last = 0
    for match in _CODE_BLOCK_RE.finditer(text):
        before = text[last : match.start()]
        if before:
            parts.append(_convert_inline_markdown(before))
        code = html.escape(match.group(1).strip("\n"), quote=False)
        parts.append(f"<pre><code>{code}</code></pre>")
        last = match.end()
    remainder = text[last:]
    if remainder:
        parts.append(_convert_inline_markdown(remainder))
    return "".join(parts)


def _looks_like_telegram_html(text: str) -> bool:
    lowered = text.lower()
    has_tag = any(tag in lowered for tag in ("<b>", "<i>", "<code>", "<pre>", "<a href="))
    has_markdown = "**" in text or "__" in text or "```" in text
    return has_tag and not has_markdown


def _convert_inline_markdown(text: str) -> str:
    parts: list[str] = []
    last = 0
    for match in _INLINE_CODE_RE.finditer(text):
        parts.append(_convert_text_styles(text[last : match.start()]))
        parts.append(f"<code>{html.escape(match.group(1), quote=False)}</code>")
        last = match.end()
    parts.append(_convert_text_styles(text[last:]))
    return "".join(parts)


def _convert_text_styles(text: str) -> str:
    if not text:
        return ""
    escaped = html.escape(text, quote=False)
    escaped = _HEADER_RE.sub(lambda match: f"<b>{match.group(1)}</b>", escaped)
    escaped = _LINK_RE.sub(
        lambda match: (
            f'<a href="{html.escape(match.group(2), quote=True)}">{match.group(1)}</a>'
        ),
        escaped,
    )
    escaped = _BOLD_RE.sub(
        lambda match: f"<b>{match.group(1) or match.group(2)}</b>",
        escaped,
    )
    return _ITALIC_RE.sub(
        lambda match: f"<i>{match.group(1) or match.group(2)}</i>",
        escaped,
    )


def sanitize_for_telegram(text: str, max_bytes: int) -> str:
    redacted = redact_secrets(text)
    formatted = markdown_to_telegram_html(redacted)
    return truncate_output(formatted, max_bytes)


def split_telegram_message(text: str, max_len: int = 4000) -> list[str]:
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_len, len(text))
        chunks.append(text[start:end])
        start = end
    return chunks


def is_forbidden_path_segment(segment: str, forbidden: Sequence[str]) -> bool:
    return segment in forbidden or segment.startswith(".")
