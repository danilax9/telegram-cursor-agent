"""Edit the in-flight agent live message from bot or worker."""

from __future__ import annotations

import uuid

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message
from redis.asyncio import Redis

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import sanitize_for_telegram
from telegram_cursor_agent.services.session_execution import SessionExecutionService
from telegram_cursor_agent.telegram.notifier import TelegramNotifier

REDIRECT_STATUS_TEXT = "↪️ Перенаправляю задачу..."
CONTINUE_STATUS_TEXT = "▶️ Продолжаю с новой инструкцией..."


async def persist_live_message_ref(
    redis: Redis,  # type: ignore[type-arg]
    session_id: uuid.UUID,
    telegram_id: int,
    message_id: int,
) -> None:
    await SessionExecutionService(redis).set_live_message(
        session_id, telegram_id, message_id
    )


async def edit_session_live_via_notifier(
    redis: Redis,  # type: ignore[type-arg]
    notifier: TelegramNotifier,
    session_id: uuid.UUID,
    text: str,
    settings: Settings,
) -> bool:
    ref = await SessionExecutionService(redis).get_live_message(session_id)
    if ref is None:
        return False
    telegram_id, message_id = ref
    safe = sanitize_for_telegram(text.strip(), settings.cursor_agent_max_output_bytes)
    if not safe:
        return False
    await notifier.edit_live_message(telegram_id, message_id, safe)
    return True


async def edit_session_live_via_message(
    redis: Redis,  # type: ignore[type-arg]
    message: Message,
    session_id: uuid.UUID,
    text: str,
    settings: Settings,
) -> bool:
    ref = await SessionExecutionService(redis).get_live_message(session_id)
    if ref is None:
        return False
    telegram_id, message_id = ref
    safe = sanitize_for_telegram(text.strip(), settings.cursor_agent_max_output_bytes)
    if not safe:
        return False
    try:
        await message.bot.edit_message_text(
            safe, chat_id=telegram_id, message_id=message_id
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return True
        try:
            await message.bot.edit_message_text(
                safe, chat_id=telegram_id, message_id=message_id, parse_mode=None
            )
        except TelegramBadRequest as retry_exc:
            if "message is not modified" in str(retry_exc).lower():
                return True
            raise
    return True
