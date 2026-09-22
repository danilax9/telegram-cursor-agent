"""Immediate Telegram typing indicator (before worker picks up the task)."""

from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.types import Message

from telegram_cursor_agent.core.logging import get_logger

logger = get_logger(__name__)


async def send_typing(message: Message) -> None:
    if message.chat is None:
        return
    await send_typing_chat(message.bot, message.chat.id)


async def send_typing_chat(bot: Bot, chat_id: int) -> None:
    try:
        await bot.send_chat_action(chat_id, ChatAction.TYPING)
    except Exception:
        logger.debug("send_typing_failed", chat_id=chat_id)
