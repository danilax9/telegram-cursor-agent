"""Agent reply delivery (rich vs legacy)."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramBadRequest

from telegram_cursor_agent.core.config import Settings, clear_settings_cache
from telegram_cursor_agent.telegram.message_delivery import send_agent_text


@pytest.fixture
def rich_settings(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    clear_settings_cache()
    monkeypatch.setenv("TELEGRAM_MESSAGE_FORMAT", "rich_markdown")
    clear_settings_cache()
    return Settings()


@pytest.fixture
def legacy_settings(test_settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    clear_settings_cache()
    monkeypatch.setenv("TELEGRAM_MESSAGE_FORMAT", "legacy")
    clear_settings_cache()
    return Settings()


async def test_send_agent_text_uses_rich_when_enabled(rich_settings: Settings) -> None:
    bot = MagicMock()
    bot.send_rich_message = AsyncMock()
    bot.send_message = AsyncMock()

    await send_agent_text(bot, 1, "**bold** reply", rich_settings)

    bot.send_rich_message.assert_awaited_once()
    bot.send_message.assert_not_awaited()


async def test_send_agent_text_falls_back_to_legacy_on_rich_error(
    rich_settings: Settings,
) -> None:
    bot = MagicMock()
    bot.send_rich_message = AsyncMock(
        side_effect=TelegramBadRequest(method=None, message="rich unsupported")
    )
    bot.send_message = AsyncMock()

    await send_agent_text(bot, 1, "plain fallback", rich_settings)

    bot.send_rich_message.assert_awaited_once()
    bot.send_message.assert_awaited_once()


async def test_send_agent_text_legacy_skips_rich(legacy_settings: Settings) -> None:
    bot = MagicMock()
    bot.send_rich_message = AsyncMock()
    bot.send_message = AsyncMock()

    await send_agent_text(bot, 1, "*legacy* text", legacy_settings)

    bot.send_rich_message.assert_not_awaited()
    bot.send_message.assert_awaited_once()
