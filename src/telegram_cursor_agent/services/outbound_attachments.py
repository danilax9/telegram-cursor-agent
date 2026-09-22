"""Parse agent output and validate files for Telegram outbound delivery."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.execution.sandbox import PathAccessError, assert_path_allowed

ATTACH_LINE_RE = re.compile(
    r"^TCA_ATTACH:(?P<path>.+?)(?:\|(?P<caption>.+))?\s*$",
    re.MULTILINE,
)

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp"})
MAX_ATTACHMENTS_PER_REPLY = 10
DEFAULT_MAX_ATTACHMENT_BYTES = 49_000_000  # below Telegram ~50 MB bot limit


@dataclass(frozen=True)
class OutboundAttachment:
    path: Path
    caption: str | None = None


@dataclass(frozen=True)
class AttachmentSkip:
    raw_path: str
    reason: str


@dataclass(frozen=True)
class ParsedOutboundAttachments:
    text: str
    attachments: list[OutboundAttachment]
    skipped: list[AttachmentSkip]


def extract_outbound_attachments(
    text: str,
    settings: Settings,
    *,
    max_bytes: int = DEFAULT_MAX_ATTACHMENT_BYTES,
    max_count: int = MAX_ATTACHMENTS_PER_REPLY,
) -> ParsedOutboundAttachments:
    """Remove TCA_ATTACH lines from agent text and resolve sendable files."""
    if not text:
        return ParsedOutboundAttachments(text="", attachments=[], skipped=[])

    attachments: list[OutboundAttachment] = []
    skipped: list[AttachmentSkip] = []
    remove_spans: list[tuple[int, int]] = []

    for match in ATTACH_LINE_RE.finditer(text):
        remove_spans.append((match.start(), match.end()))
        if len(attachments) >= max_count:
            skipped.append(
                AttachmentSkip(
                    match.group("path").strip(),
                    f"лимит {max_count} файлов за ответ",
                )
            )
            continue

        raw_path = match.group("path").strip()
        caption = (match.group("caption") or "").strip() or None
        try:
            resolved = assert_path_allowed(raw_path, settings)
        except PathAccessError:
            skipped.append(AttachmentSkip(raw_path, "путь вне разрешённых каталогов"))
            continue

        if not resolved.is_file():
            skipped.append(AttachmentSkip(raw_path, "файл не найден"))
            continue

        size = resolved.stat().st_size
        if size > max_bytes:
            skipped.append(
                AttachmentSkip(
                    raw_path,
                    f"слишком большой ({size} байт, лимит {max_bytes})",
                )
            )
            continue

        attachments.append(OutboundAttachment(path=resolved, caption=caption))

    cleaned = _strip_spans(text, remove_spans).strip()
    return ParsedOutboundAttachments(
        text=cleaned,
        attachments=attachments,
        skipped=skipped,
    )


def format_skipped_attachments(skipped: list[AttachmentSkip]) -> str:
    if not skipped:
        return ""
    lines = ["", "⚠️ Файлы не отправлены:"]
    for item in skipped:
        lines.append(f"• `{item.raw_path}` — {item.reason}")
    return "\n".join(lines)


def is_image_attachment(path: Path) -> bool:
    return path.suffix.lower() in IMAGE_SUFFIXES


def _strip_spans(text: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return text
    parts: list[str] = []
    last = 0
    for start, end in sorted(spans):
        parts.append(text[last:start])
        last = end
    parts.append(text[last:])
    return "".join(parts)
