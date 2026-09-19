"""Live Telegram message update tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram_cursor_agent.telegram.live_message import LiveMessageNotifier
from telegram_cursor_agent.telegram.notifier import TelegramNotifier


@pytest.fixture
def live_notifier(test_settings) -> LiveMessageNotifier:
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.send_live_start = AsyncMock(return_value=42)
    notifier.edit_live_message = AsyncMock()
    return LiveMessageNotifier(notifier, test_settings, telegram_id=12345)


async def test_first_progress_sends_message(live_notifier: LiveMessageNotifier) -> None:
    await live_notifier.update("Шаг 1")
    live_notifier._notifier.send_live_start.assert_awaited_once()  # type: ignore[attr-defined]
    assert live_notifier.message_id == 42


async def test_next_progress_edits_message(live_notifier: LiveMessageNotifier) -> None:
    await live_notifier.update("Шаг 1")
    await live_notifier.update("Шаг 2")
    live_notifier._notifier.edit_live_message.assert_awaited_once_with(  # type: ignore[attr-defined]
        12345, 42, "Шаг 2"
    )


async def test_duplicate_progress_is_ignored(live_notifier: LiveMessageNotifier) -> None:
    await live_notifier.update("Шаг 1")
    await live_notifier.update("Шаг 1")
    live_notifier._notifier.edit_live_message.assert_not_awaited()  # type: ignore[attr-defined]
