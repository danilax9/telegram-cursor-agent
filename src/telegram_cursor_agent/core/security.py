"""Authorization and output redaction."""

import re
from collections.abc import Sequence

from telegram_cursor_agent.core.config import Settings

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


def sanitize_for_telegram(text: str, max_bytes: int) -> str:
    redacted = redact_secrets(text)
    return truncate_output(redacted, max_bytes)


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
