import httpx
from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.prompts import HELP_TEXT
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import sanitize_for_telegram, split_telegram_message
from telegram_cursor_agent.database.repositories.user import UserRepository
from redis.asyncio import Redis

from telegram_cursor_agent.services.cursor_accounts import CursorAccountService
from telegram_cursor_agent.services.cursor_models import CursorModelsError, load_models
from telegram_cursor_agent.telegram.model_keyboards import (
    model_families_keyboard,
    model_selection_text,
)
from telegram_cursor_agent.services.usage import (
    CursorUsageError,
    format_usage_message,
)
from telegram_cursor_agent.telegram.keyboards import main_menu_keyboard
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
    is_owner = telegram_user_id in settings.telegram_admin_ids
    await users.upsert(
        telegram_id=telegram_user_id,
        username=message.from_user.username if message.from_user else None,
        is_admin=True if is_owner else None,
    )
    await message.answer(START_MESSAGE, reply_markup=main_menu_keyboard())


@router.message(Command("model"))
async def cmd_model(message: Message, settings: Settings) -> None:
    try:
        models = load_models(settings)
    except CursorModelsError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        model_selection_text(models),
        reply_markup=model_families_keyboard(models),
    )


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    await message.answer(HELP_TEXT)


@router.message(Command("limits"))
async def cmd_limits(
    message: Message,
    settings: Settings,
    redis_client: Redis,  # type: ignore[type-arg]
) -> None:
    accounts = CursorAccountService(settings, redis_client)
    try:
        active = await accounts.get_active_account()
        snapshot = await accounts.fetch_usage(active)
    except CursorUsageError as exc:
        await message.answer(str(exc))
        return
    except httpx.HTTPError:
        await message.answer("Не удалось получить лимиты Cursor. Попробуй позже.")
        return

    usage_text = format_usage_message(snapshot)
    text = sanitize_for_telegram(
        f"Аккаунт: `{active.id}` ({active.label})\n\n{usage_text}",
        settings.cursor_agent_max_output_bytes,
    )
    for chunk in split_telegram_message(text):
        await message.answer(chunk)
