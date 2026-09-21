"""Telegram bot setup."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.telegram.handlers import setup_routers
from telegram_cursor_agent.telegram.middlewares.admin import AdminAuthMiddleware
from telegram_cursor_agent.telegram.middlewares.db import DbSessionMiddleware


def create_bot(settings: Settings) -> Bot:
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
    )


def create_dispatcher(
    settings: Settings,
    session_factory: async_sessionmaker[AsyncSession],
    task_queue: TaskQueue,
    runner: ProcessRunner,
    redis_client: Redis,  # type: ignore[type-arg]
) -> Dispatcher:
    dp = Dispatcher()
    admin_middleware = AdminAuthMiddleware(settings)
    db_middleware = DbSessionMiddleware(session_factory)
    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(db_middleware)
        observer.middleware(admin_middleware)

    async def inject_services(
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["settings"] = settings
        data["task_queue"] = task_queue
        data["runner"] = runner
        data["redis_client"] = redis_client
        return await handler(event, data)

    for observer in (dp.message, dp.callback_query):
        observer.outer_middleware(inject_services)

    dp.include_router(setup_routers())
    return dp
