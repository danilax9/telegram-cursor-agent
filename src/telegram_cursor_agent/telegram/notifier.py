"""Telegram delivery for asynchronous Cursor task results."""

import asyncio

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import sanitize_for_telegram, split_telegram_message


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

    async def send(self, telegram_id: int, text: str) -> None:
        safe_text = sanitize_for_telegram(text, self._settings.cursor_agent_max_output_bytes)
        for chunk in split_telegram_message(safe_text):
            try:
                await self._bot.send_message(telegram_id, chunk)
            except TelegramBadRequest:
                # Cursor occasionally emits invalid HTML despite its prompt.
                # Deliver the content rather than failing the whole task.
                await self._bot.send_message(telegram_id, chunk, parse_mode=None)

    async def send_live_start(self, telegram_id: int, text: str) -> int:
        try:
            message = await self._bot.send_message(telegram_id, text)
        except TelegramBadRequest:
            message = await self._bot.send_message(telegram_id, text, parse_mode=None)
        return message.message_id

    async def edit_live_message(
        self, telegram_id: int, message_id: int, text: str
    ) -> None:
        try:
            await self._bot.edit_message_text(
                text, chat_id=telegram_id, message_id=message_id
            )
        except TelegramBadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            try:
                await self._bot.edit_message_text(
                    text,
                    chat_id=telegram_id,
                    message_id=message_id,
                    parse_mode=None,
                )
            except TelegramBadRequest as retry_exc:
                if "message is not modified" not in str(retry_exc).lower():
                    raise

    async def keep_typing(self, telegram_id: int) -> None:
        while True:
            await self._bot.send_chat_action(telegram_id, "typing")
            await asyncio.sleep(4)

    async def close(self) -> None:
        await self._bot.session.close()
