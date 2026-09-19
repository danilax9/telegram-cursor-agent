import asyncio
import json

import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.prompts import HELP_TEXT
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import sanitize_for_telegram, split_telegram_message
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.services.usage import (
    CursorUsageError,
    CursorUsageService,
    format_usage_message,
)
from telegram_cursor_agent.telegram.keyboards import main_menu_keyboard, model_keyboard
from telegram_cursor_agent.telegram.messages import START_MESSAGE

router = Router()


@router.message(Command("start"))
async def cmd_start(
    message: Message,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
) -> None:
    users = UserRepository(db)
    is_admin_user = telegram_user_id in settings.telegram_admin_ids
    await users.upsert(
        telegram_id=telegram_user_id,
        username=message.from_user.username if message.from_user else None,
        is_admin=is_admin_user,
    )
    await message.answer(START_MESSAGE, reply_markup=main_menu_keyboard())


@router.message(Command("model"))
async def cmd_model(message: Message, settings: Settings) -> None:
    models_path = settings.projects_root / "cursor-models.json"
    raw = await asyncio.to_thread(models_path.read_text)
    models = json.loads(raw)
    await message.answer("Выбери модель Cursor", reply_markup=model_keyboard(models))


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("limits"))
async def cmd_limits(message: Message, settings: Settings) -> None:
    service = CursorUsageService(settings)
    try:
        snapshot = await service.fetch_usage()
    except CursorUsageError as exc:
        await message.answer(str(exc))
        return
    except httpx.HTTPError:
        await message.answer("Не удалось получить лимиты Cursor. Попробуй позже.")
        return

    text = sanitize_for_telegram(
        format_usage_message(snapshot),
        settings.cursor_agent_max_output_bytes,
    )
    for chunk in split_telegram_message(text):
        await message.answer(chunk)
