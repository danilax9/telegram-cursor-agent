"""Admin-only authorization middleware."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import is_authorized
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.telegram.messages import UNAUTHORIZED_MESSAGE


class AdminAuthMiddleware(BaseMiddleware):
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id: int | None = None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            user_id = event.from_user.id

        if user_id is None:
            return None

        db: AsyncSession | None = data.get("db")
        user = None
        if db is not None:
            user = await UserRepository(db).get_by_telegram_id(user_id)

        if not is_authorized(user_id, self._settings, user):
            if isinstance(event, Message):
                await event.answer(UNAUTHORIZED_MESSAGE)
            elif isinstance(event, CallbackQuery):
                await event.answer(UNAUTHORIZED_MESSAGE, show_alert=True)
            return None

        data["telegram_user_id"] = user_id
        return await handler(event, data)
