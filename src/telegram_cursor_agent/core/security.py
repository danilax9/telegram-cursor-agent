"""Authorization and output redaction."""

import re
from collections.abc import Sequence

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.models.user import User

_TABLE_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_RE = re.compile(r"^\s*\|[-:\s|]+\|\s*$")
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_MARKDOWN_ITALIC_RE = re.compile(
    r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)|(?<!_)_(?!_)(.+?)(?<!_)_(?!_)"
)
_MARKDOWN_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
_MARKDOWN_CODE_FENCE_RE = re.compile(r"```(?:[\w-]*\n)?(.*?)```", re.DOTALL)
_MARKDOWN_INLINE_CODE_RE = re.compile(r"`([^`\n]+)`")

_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|passwd)\s*[:=]\s*\S+"),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]+"),
]


def is_super_admin(telegram_user_id: int, settings: Settings) -> bool:
    return telegram_user_id in settings.telegram_admin_ids


def is_admin(telegram_user_id: int, settings: Settings, user: User | None = None) -> bool:
    if is_super_admin(telegram_user_id, settings):
        return True
    return user is not None and user.is_admin


def is_authorized(telegram_user_id: int, settings: Settings, user: User | None = None) -> bool:
    return is_admin(telegram_user_id, settings, user)


def require_admin(
    telegram_user_id: int, settings: Settings, user: User | None = None
) -> None:
    if not is_authorized(telegram_user_id, settings, user):
        raise PermissionError(f"User {telegram_user_id} is not authorized")


def require_super_admin(telegram_user_id: int, settings: Settings) -> None:
    if not is_super_admin(telegram_user_id, settings):
        raise PermissionError("Only the bot owner can manage access.")


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


def _flatten_code_fence(body: str) -> str:
    lines = body.strip().splitlines() or [""]
    return "\n".join(f"`{line.replace('`', "'")}`" if line else "" for line in lines)


def strip_unsupported_markdown(text: str) -> str:
    """Normalize Markdown for Telegram legacy mode (no ``` fences)."""
    if not text:
        return text

    lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if _TABLE_SEP_RE.match(stripped):
            continue
        table_match = _TABLE_ROW_RE.match(stripped)
        if table_match:
            cells = [cell.strip() for cell in table_match.group(1).split("|") if cell.strip()]
            if cells:
                lines.append("• " + " | ".join(cells))
            continue
        lines.append(line)

    cleaned = "\n".join(lines)
    cleaned = _MARKDOWN_CODE_FENCE_RE.sub(
        lambda match: _flatten_code_fence(match.group(1)),
        cleaned,
    )
    cleaned = _MARKDOWN_HEADING_RE.sub(r"\1", cleaned)
    cleaned = _MARKDOWN_LINK_RE.sub(r"\1 (\2)", cleaned)
    cleaned = _MARKDOWN_BOLD_RE.sub(lambda match: match.group(1) or match.group(2) or "", cleaned)
    cleaned = _MARKDOWN_ITALIC_RE.sub(
        lambda match: match.group(1) or match.group(2) or "",
        cleaned,
    )
    return cleaned


def escape_telegram_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


_MD_V2_ESCAPE_CHARS = frozenset(r"_*[]()~`>#+-=|{}.!\\")


def escape_telegram_markdown_v2(text: str) -> str:
    """Escape text for Telegram MarkdownV2 (outside pre-formatted entities)."""
    parts: list[str] = []
    for char in text:
        if char in _MD_V2_ESCAPE_CHARS:
            parts.append(f"\\{char}")
        else:
            parts.append(char)
    return "".join(parts)


def sanitize_for_telegram_html(text: str, max_bytes: int) -> str:
    redacted = redact_secrets(text)
    return truncate_output(redacted, max_bytes)


def sanitize_for_telegram_markdown_v2(text: str, max_bytes: int) -> str:
    redacted = redact_secrets(text)
    return truncate_output(redacted, max_bytes)


def sanitize_for_telegram(text: str, max_bytes: int) -> str:
    redacted = redact_secrets(text)
    cleaned = strip_unsupported_markdown(redacted)
    return truncate_output(cleaned, max_bytes)


def sanitize_for_telegram_rich(text: str, max_bytes: int) -> str:
    """Rich messages accept normal Markdown; only redact secrets and truncate."""
    redacted = redact_secrets(text)
    return truncate_output(redacted, max_bytes)


def prepare_agent_reply_text(text: str, settings: Settings) -> str:
    max_bytes = settings.cursor_agent_max_output_bytes
    if settings.telegram_uses_rich_messages:
        return sanitize_for_telegram_rich(text, max_bytes)
    return sanitize_for_telegram(text, max_bytes)


TELEGRAM_MESSAGE_MAX_CHARS = 4096


def strip_live_tool_quotes(display_text: str) -> str:
    """Drop blockquoted tool-call lines from a composed live MarkdownV2 message."""
    lines = display_text.split("\n")
    kept = [line for line in lines if not line.startswith(">")]
    return "\n".join(kept).strip()


def strip_live_tool_section(display_text: str) -> str:
    """Drop tool-call section from live preview (MarkdownV2 quotes or Rich Details)."""
    marker = "# Details"
    index = display_text.find(marker)
    if index != -1:
        return display_text[:index].strip()
    return strip_live_tool_quotes(display_text)


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
