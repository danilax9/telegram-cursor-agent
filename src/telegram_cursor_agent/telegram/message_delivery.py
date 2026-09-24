"""Deliver agent replies via legacy Markdown or Bot API rich messages."""

from __future__ import annotations

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputRichMessage, ReplyMarkupUnion

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.logging import get_logger
from telegram_cursor_agent.core.security import (
    prepare_agent_reply_text,
    split_telegram_message,
)

logger = get_logger(__name__)


async def send_agent_text(
    bot: Bot,
    chat_id: int,
    text: str,
    settings: Settings,
    *,
    reply_markup: ReplyMarkupUnion | None = None,
    rich_html: bool = False,
) -> None:
    """Send a user-facing agent reply with rich-message support and fallbacks."""
    safe = prepare_agent_reply_text(text, settings)
    chunks = split_telegram_message(safe)
    for index, chunk in enumerate(chunks):
        markup = reply_markup if index == 0 else None
        if settings.telegram_uses_rich_messages:
            if await _try_send_rich(
                bot, chat_id, chunk, reply_markup=markup, html=rich_html
            ):
                continue
        await _send_legacy(bot, chat_id, chunk, reply_markup=markup)


async def _try_send_rich(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    reply_markup: ReplyMarkupUnion | None,
    html: bool = False,
) -> bool:
    payload = InputRichMessage(html=text) if html else InputRichMessage(markdown=text)
    try:
        await bot.send_rich_message(
            chat_id,
            payload,
            reply_markup=reply_markup,
        )
        return True
    except TelegramBadRequest as exc:
        logger.warning(
            "telegram_rich_message_failed",
            chat_id=chat_id,
            error=str(exc),
        )
        return False


async def _send_legacy(
    bot: Bot,
    chat_id: int,
    text: str,
    *,
    reply_markup: ReplyMarkupUnion | None,
) -> None:
    try:
        await bot.send_message(chat_id, text, reply_markup=reply_markup)
    except TelegramBadRequest:
        await bot.send_message(
            chat_id,
            text,
            reply_markup=reply_markup,
            parse_mode=None,
        )
