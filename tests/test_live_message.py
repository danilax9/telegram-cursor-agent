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
        12345,
        "*💬 Шаг 1*",
        markdown_v2=False,
        rich_markdown=True,
        rich_html=False,
    )
    assert live_notifier.message_id == 42


async def test_next_progress_edits_message(live_notifier: LiveMessageNotifier) -> None:
    await live_notifier.update("Шаг 1")
    await live_notifier.update("Шаг 2")
    live_notifier._notifier.edit_live_message.assert_awaited_once_with(  # type: ignore[attr-defined]
        12345,
        42,
        "*💬 Шаг 2*",
        markdown_v2=False,
        rich_markdown=True,
        rich_html=False,
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
        12345,
        "*↪️ Перенаправляю задачу...*",
        markdown_v2=False,
        rich_markdown=True,
        rich_html=False,
    )


async def test_status_error_does_not_escape(live_notifier: LiveMessageNotifier) -> None:
    live_notifier._notifier.send_live_start.side_effect = TypeError(  # type: ignore[attr-defined]
        "unexpected keyword argument 'rich_html'"
    )
    await live_notifier.update_status(THINKING_STATUS_TEXT)


async def test_thinking_status_on_worker_start(live_notifier: LiveMessageNotifier) -> None:
    await live_notifier.update_status(THINKING_STATUS_TEXT)
    live_notifier._notifier.send_live_start.assert_awaited_once_with(  # type: ignore[attr-defined]
        12345,
        f"*{THINKING_STATUS_TEXT}*",
        markdown_v2=False,
        rich_markdown=True,
        rich_html=False,
    )
    await live_notifier.update("Первый шаг")
    live_notifier._notifier.edit_live_message.assert_awaited_once_with(  # type: ignore[attr-defined]
        12345,
        42,
        "*💬 Первый шаг*",
        markdown_v2=False,
        rich_markdown=True,
        rich_html=False,
    )


async def test_finalize_sends_new_message_and_deletes_progress(
    test_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from telegram_cursor_agent.core.config import Settings, clear_settings_cache

    clear_settings_cache()
    monkeypatch.setenv("TELEGRAM_MESSAGE_FORMAT", "rich_markdown")
    clear_settings_cache()
    settings = Settings()

    notifier = MagicMock(spec=TelegramNotifier)
    notifier.send_live_start = AsyncMock(return_value=42)
    notifier.edit_live_message = AsyncMock()
    notifier.send = AsyncMock()
    notifier.delete_message = AsyncMock()
    live = LiveMessageNotifier(notifier, settings, telegram_id=12345)

    await live.update("Промежуточный шаг")
    edits_before_finalize = notifier.edit_live_message.await_count
    await live.finalize("**Итог**")

    notifier.send.assert_awaited_once_with(12345, "**Итог**")
    notifier.delete_message.assert_awaited_once_with(12345, 42)
    assert notifier.edit_live_message.await_count == edits_before_finalize


async def test_live_tool_calls_use_rich_when_enabled(
    test_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from telegram_cursor_agent.core.config import Settings, clear_settings_cache

    clear_settings_cache()
    monkeypatch.setenv("TELEGRAM_MESSAGE_FORMAT", "rich_markdown")
    monkeypatch.setenv("TELEGRAM_LIVE_TOOL_DEBOUNCE_SECONDS", "0")
    clear_settings_cache()
    settings = Settings()

    notifier = MagicMock(spec=TelegramNotifier)
    notifier.send_live_start = AsyncMock(return_value=42)
    notifier.edit_live_message = AsyncMock()
    live = LiveMessageNotifier(
        notifier, settings, telegram_id=12345, tool_calls_in_live=True
    )

    await live.replace_display('<blockquote expandable><strong>x</strong></blockquote>')

    notifier.send_live_start.assert_awaited_once_with(
        12345,
        '<blockquote expandable><strong>x</strong></blockquote>',
        markdown_v2=False,
        rich_markdown=False,
        rich_html=True,
    )
