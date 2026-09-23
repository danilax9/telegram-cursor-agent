"""Live Telegram message update tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram_cursor_agent.telegram.live_message import (
    LiveMessageNotifier,
    THINKING_STATUS_TEXT,
    format_progress_message,
)
from telegram_cursor_agent.telegram.notifier import TelegramNotifier


@pytest.fixture
def live_notifier(test_settings) -> LiveMessageNotifier:
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.send_live_start = AsyncMock(return_value=42)
    notifier.edit_live_message = AsyncMock()
    return LiveMessageNotifier(notifier, test_settings, telegram_id=12345)


def test_format_progress_message_adds_speech_prefix() -> None:
    assert format_progress_message("Проверю код.") == "💬 Проверю код."


def test_format_progress_message_keeps_existing_prefix() -> None:
    assert format_progress_message("💬 Уже с префиксом") == "💬 Уже с префиксом"


def test_format_progress_message_normalizes_legacy_thought_prefix() -> None:
    assert format_progress_message("💭 Старый префикс") == "💬 Старый префикс"


async def test_first_progress_sends_message(live_notifier: LiveMessageNotifier) -> None:
    await live_notifier.update("Шаг 1")
    live_notifier._notifier.send_live_start.assert_awaited_once_with(  # type: ignore[attr-defined]
        12345, "💬 Шаг 1", markdown_v2=False
    )
    assert live_notifier.message_id == 42


async def test_next_progress_edits_message(live_notifier: LiveMessageNotifier) -> None:
    await live_notifier.update("Шаг 1")
    await live_notifier.update("Шаг 2")
    live_notifier._notifier.edit_live_message.assert_awaited_once_with(  # type: ignore[attr-defined]
        12345, 42, "💬 Шаг 2", markdown_v2=False
    )


async def test_duplicate_progress_is_ignored(live_notifier: LiveMessageNotifier) -> None:
    await live_notifier.update("Шаг 1")
    await live_notifier.update("Шаг 1")
    live_notifier._notifier.edit_live_message.assert_not_awaited()  # type: ignore[attr-defined]


async def test_status_update_without_speech_prefix(
    live_notifier: LiveMessageNotifier,
) -> None:
    await live_notifier.update_status("↪️ Перенаправляю задачу...")
    live_notifier._notifier.send_live_start.assert_awaited_once_with(  # type: ignore[attr-defined]
        12345, "↪️ Перенаправляю задачу...", markdown_v2=False
    )


async def test_thinking_status_on_worker_start(live_notifier: LiveMessageNotifier) -> None:
    await live_notifier.update_status(THINKING_STATUS_TEXT)
    live_notifier._notifier.send_live_start.assert_awaited_once_with(  # type: ignore[attr-defined]
        12345, THINKING_STATUS_TEXT, markdown_v2=False
    )
    await live_notifier.update("Первый шаг")
    live_notifier._notifier.edit_live_message.assert_awaited_once_with(  # type: ignore[attr-defined]
        12345, 42, "💬 Первый шаг", markdown_v2=False
    )
