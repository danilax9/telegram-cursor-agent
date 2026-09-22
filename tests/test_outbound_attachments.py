"""Outbound Telegram attachment parsing."""

from pathlib import Path

import pytest

from telegram_cursor_agent.services.outbound_attachments import (
    extract_outbound_attachments,
    format_skipped_attachments,
)
from telegram_cursor_agent.telegram.notifier import TelegramNotifier


def test_extract_removes_attach_lines_and_resolves_file(
    test_settings, tmp_workspace: Path
) -> None:
    sample = tmp_workspace / "out.txt"
    sample.write_text("hello", encoding="utf-8")
    text = f"Готово.\nTCA_ATTACH:{sample}\n"
    parsed = extract_outbound_attachments(text, test_settings)
    assert parsed.text == "Готово."
    assert len(parsed.attachments) == 1
    assert parsed.attachments[0].path == sample.resolve()
    assert parsed.skipped == []


def test_extract_skips_missing_file(test_settings, tmp_workspace: Path) -> None:
    missing = tmp_workspace / "nope.bin"
    text = f"Done\nTCA_ATTACH:{missing}\n"
    parsed = extract_outbound_attachments(text, test_settings)
    assert parsed.attachments == []
    assert len(parsed.skipped) == 1
    assert "не найден" in parsed.skipped[0].reason


def test_extract_caption_and_relative_path(test_settings, tmp_workspace: Path) -> None:
    sample = tmp_workspace / "a.pdf"
    sample.write_bytes(b"%PDF")
    text = "TCA_ATTACH:a.pdf|Monthly report"
    parsed = extract_outbound_attachments(text, test_settings)
    assert parsed.attachments[0].caption == "Monthly report"


def test_format_skipped_attachments() -> None:
    from telegram_cursor_agent.services.outbound_attachments import AttachmentSkip

    msg = format_skipped_attachments(
        [AttachmentSkip("/x", "файл не найден")]
    )
    assert "не отправлены" in msg
    assert "/x" in msg


@pytest.mark.asyncio
async def test_send_attachments_uses_document(
    test_settings, tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from unittest.mock import AsyncMock

    from telegram_cursor_agent.services.outbound_attachments import OutboundAttachment

    path = tmp_workspace / "data.csv"
    path.write_text("a,b\n1,2\n", encoding="utf-8")
    settings = test_settings.model_copy(update={"bot_token": "123456789:AAHfakeTokenForTests"})
    notifier = TelegramNotifier(settings)
    send_document = AsyncMock()
    send_photo = AsyncMock()
    monkeypatch.setattr(notifier._bot, "send_document", send_document)
    monkeypatch.setattr(notifier._bot, "send_photo", send_photo)

    errors = await notifier.send_attachments(
        123, [OutboundAttachment(path=path)]
    )
    assert errors == []
    send_document.assert_awaited_once()
    send_photo.assert_not_awaited()
