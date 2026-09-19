"""Telegram handlers."""

from aiogram import Router

from telegram_cursor_agent.telegram.handlers.callbacks import router as callbacks_router
from telegram_cursor_agent.telegram.handlers.commands import router as commands_router
from telegram_cursor_agent.telegram.handlers.messages import router as messages_router
from telegram_cursor_agent.telegram.handlers.uploads import router as uploads_router


def setup_routers() -> Router:
    root = Router()
    root.include_router(commands_router)
    root.include_router(callbacks_router)
    root.include_router(uploads_router)
    root.include_router(messages_router)
    return root
