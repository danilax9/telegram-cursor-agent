"""Telegram delivery for asynchronous Cursor task results."""

import asyncio

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ChatAction, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup, InputRichMessage

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.logging import get_logger
from telegram_cursor_agent.core.security import (
    TELEGRAM_MESSAGE_MAX_CHARS,
    prepare_agent_reply_text,
    split_telegram_message,
    strip_live_tool_section,
)
from telegram_cursor_agent.services.outbound_attachments import (
    OutboundAttachment,
    is_image_attachment,
)
from telegram_cursor_agent.telegram.message_delivery import send_agent_text

logger = get_logger(__name__)


def _is_message_too_long(exc: TelegramBadRequest) -> bool:
    return "messagetoolong" in str(exc).lower()


async def _edit_live_rich_with_fallback(
    bot: Bot,
    *,
    telegram_id: int,
    message_id: int,
    markdown: str,
    reply_markup: InlineKeyboardMarkup | None,
) -> None:
    try:
        await bot.edit_message_text(
            chat_id=telegram_id,
            message_id=message_id,
            rich_message=InputRichMessage(markdown=markdown),
            reply_markup=reply_markup,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        if _is_message_too_long(exc):
            without_tools = strip_live_tool_section(markdown)
            if without_tools and without_tools != markdown:
                await _edit_live_rich_with_fallback(
                    bot,
                    telegram_id=telegram_id,
                    message_id=message_id,
                    markdown=without_tools,
                    reply_markup=reply_markup,
                )
                return
        try:
            await bot.edit_message_text(
                chat_id=telegram_id,
                message_id=message_id,
                text=markdown,
                reply_markup=reply_markup,
                parse_mode=None,
            )
        except TelegramBadRequest as retry_exc:
            if "message is not modified" in str(retry_exc).lower():
                return
            raise


async def _edit_live_text_with_fallback(
    bot: Bot,
    *,
    telegram_id: int,
    message_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None,
    markdown_v2: bool,
) -> None:
    mode = ParseMode.MARKDOWN_V2 if markdown_v2 else None
    try:
        await bot.edit_message_text(
            text,
            chat_id=telegram_id,
            message_id=message_id,
            reply_markup=reply_markup,
            parse_mode=mode,
        )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        if markdown_v2 and _is_message_too_long(exc):
            without_tools = strip_live_tool_section(text)
            if without_tools and without_tools != text:
                await _edit_live_text_with_fallback(
                    bot,
                    telegram_id=telegram_id,
                    message_id=message_id,
                    text=without_tools,
                    reply_markup=reply_markup,
                    markdown_v2=True,
                )
                return
        try:
            await bot.edit_message_text(
                text,
                chat_id=telegram_id,
                message_id=message_id,
                reply_markup=reply_markup,
                parse_mode=None,
            )
        except TelegramBadRequest as retry_exc:
            if "message is not modified" in str(retry_exc).lower():
                return
            if _is_message_too_long(retry_exc) and len(text) > TELEGRAM_MESSAGE_MAX_CHARS:
                clipped = text[: TELEGRAM_MESSAGE_MAX_CHARS - 1] + "…"
                await bot.edit_message_text(
                    clipped,
                    chat_id=telegram_id,
                    message_id=message_id,
                    reply_markup=reply_markup,
                    parse_mode=None,
                )
                return
            raise


class TelegramNotifier:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._bot = Bot(
            token=settings.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
        )

    async def send(self, telegram_id: int, text: str) -> None:
        await send_agent_text(self._bot, telegram_id, text, self._settings)

    async def send_with_url_button(
        self,
        telegram_id: int,
        text: str,
        *,
        button_text: str,
        url: str,
    ) -> None:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[InlineKeyboardButton(text=button_text, url=url)]]
        )
        await send_agent_text(
            self._bot,
            telegram_id,
            text,
            self._settings,
            reply_markup=keyboard,
        )

    async def send_live_start(
        self,
        telegram_id: int,
        text: str,
        *,
        markdown_v2: bool = False,
        rich_markdown: bool = False,
    ) -> int:
        if rich_markdown:
            try:
                message = await self._bot.send_rich_message(
                    telegram_id, InputRichMessage(markdown=text)
                )
                return message.message_id
            except TelegramBadRequest as exc:
                if _is_message_too_long(exc):
                    without_tools = strip_live_tool_section(text)
                    if without_tools and without_tools != text:
                        message = await self._bot.send_rich_message(
                            telegram_id, InputRichMessage(markdown=without_tools)
                        )
                        return message.message_id
                message = await self._bot.send_message(telegram_id, text, parse_mode=None)
                return message.message_id
        mode = ParseMode.MARKDOWN_V2 if markdown_v2 else None
        try:
            message = await self._bot.send_message(
                telegram_id, text, parse_mode=mode
            )
        except TelegramBadRequest as exc:
            if markdown_v2 and _is_message_too_long(exc):
                without_tools = strip_live_tool_section(text)
                if without_tools and without_tools != text:
                    message = await self._bot.send_message(
                        telegram_id, without_tools, parse_mode=ParseMode.MARKDOWN_V2
                    )
                    return message.message_id
            message = await self._bot.send_message(telegram_id, text, parse_mode=None)
        return message.message_id

    async def edit_live_message(
        self,
        telegram_id: int,
        message_id: int,
        text: str,
        *,
        reply_markup: InlineKeyboardMarkup | None = None,
        markdown_v2: bool = False,
        rich_markdown: bool = False,
    ) -> None:
        if rich_markdown:
            await _edit_live_rich_with_fallback(
                self._bot,
                telegram_id=telegram_id,
                message_id=message_id,
                markdown=text,
                reply_markup=reply_markup,
            )
            return
        await _edit_live_text_with_fallback(
            self._bot,
            telegram_id=telegram_id,
            message_id=message_id,
            text=text,
            reply_markup=reply_markup,
            markdown_v2=markdown_v2,
        )

    async def send_attachments(
        self,
        telegram_id: int,
        attachments: list[OutboundAttachment],
    ) -> list[str]:
        """Upload files to Telegram. Returns human-readable errors for failures."""
        errors: list[str] = []
        for attachment in attachments:
            path = attachment.path
            caption = attachment.caption
            file_input = FSInputFile(path, filename=path.name)
            try:
                if is_image_attachment(path):
                    await self._bot.send_photo(
                        telegram_id,
                        file_input,
                        caption=caption,
                    )
                else:
                    await self._bot.send_document(
                        telegram_id,
                        file_input,
                        caption=caption,
                    )
            except TelegramBadRequest as exc:
                msg = f"`{path}` — Telegram отклонил файл: {exc}"
                errors.append(msg)
                logger.warning(
                    "telegram_attachment_rejected",
                    path=str(path),
                    error=str(exc),
                )
            except Exception as exc:
                msg = f"`{path}` — ошибка отправки: {exc}"
                errors.append(msg)
                logger.exception("telegram_attachment_failed", path=str(path))
        return errors

    async def send_typing_once(self, telegram_id: int) -> None:
        try:
            await self._bot.send_chat_action(telegram_id, ChatAction.TYPING)
        except Exception:
            pass

    async def keep_typing(self, telegram_id: int) -> None:
        try:
            while True:
                try:
                    await asyncio.wait_for(
                        self._bot.send_chat_action(telegram_id, ChatAction.TYPING),
                        timeout=10.0,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Typing is cosmetic: never let a transient API error end the loop.
                    pass
                await asyncio.sleep(3)
        except asyncio.CancelledError:
            raise

    async def close(self) -> None:
        await self._bot.session.close()
