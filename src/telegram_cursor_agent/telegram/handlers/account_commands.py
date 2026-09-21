"""Telegram /account management commands."""

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import require_super_admin, split_telegram_message
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.cursor_account_login import (
    CursorAccountLoginError,
    CursorAccountLoginService,
)
from telegram_cursor_agent.services.cursor_accounts import CursorAccountError, CursorAccountService
from telegram_cursor_agent.services.usage import CursorUsageError, format_usage_message

router = Router()


@router.message(Command("account"))
async def cmd_account(
    message: Message,
    command: CommandObject,
    settings: Settings,
    telegram_user_id: int,
    redis_client: Redis,  # type: ignore[type-arg]
    db: AsyncSession,
    task_queue: TaskQueue,
) -> None:
    try:
        require_super_admin(telegram_user_id, settings)
    except PermissionError:
        await message.answer("Только владелец бота может управлять аккаунтами Cursor.")
        return

    service = CursorAccountService(settings, redis_client)
    login_service = CursorAccountLoginService(settings, redis_client)
    args = (command.args or "").strip()
    if not args:
        text = await service.format_accounts_message()
        for chunk in split_telegram_message(text):
            await message.answer(chunk)
        return

    parts = args.split(maxsplit=1)
    action = parts[0].lower()
    target = parts[1].strip() if len(parts) > 1 else ""

    try:
        if action in {"use", "switch", "set"}:
            if not target:
                await message.answer("Укажи id: `/account use main`")
                return
            account = await service.set_active_account(target)
            await message.answer(
                f"Активный аккаунт Cursor: *{account.label}* (`{account.id}`)"
            )
            return

        if action == "limits":
            lines = ["*Лимиты всех аккаунтов Cursor*", ""]
            for account in service.list_accounts():
                try:
                    snapshot = await service.fetch_usage(account)
                    lines.append(f"*{account.id}* ({account.label})")
                    lines.append(format_usage_message(snapshot))
                    lines.append("")
                except CursorUsageError as exc:
                    lines.append(f"*{account.id}*: {exc}")
                    lines.append("")
            for chunk in split_telegram_message("\n".join(lines).strip()):
                await message.answer(chunk)
            return

        if action == "cancel":
            if login_service.is_cli_available():
                text = await login_service.cancel_login(telegram_user_id)
            else:
                user = await UserRepository(db).get_by_telegram_id(telegram_user_id)
                if user is None:
                    await message.answer("Сначала отправь /start.")
                    return
                text = await login_service.queue_login_cancel(
                    db, task_queue, user.id, telegram_user_id
                )
            await message.answer(text)
            return

        if action in {"add", "login", "prepare"}:
            if not target:
                await message.answer("Укажи id: `/account add backup`")
                return
            if login_service.is_cli_available():
                result = await login_service.start_login(telegram_user_id, target)
                await login_service.notify_login_start(telegram_user_id, result)
            else:
                user = await UserRepository(db).get_by_telegram_id(telegram_user_id)
                if user is None:
                    await message.answer("Сначала отправь /start.")
                    return
                text = await login_service.queue_login_start(
                    db, task_queue, user.id, telegram_user_id, target
                )
                await message.answer(text)
            return

        await message.answer(
            "Команды:\n"
            "• `/account` — список аккаунтов\n"
            "• `/account add <id>` — добавить через браузер\n"
            "• `/account use <id>` — переключить\n"
            "• `/account limits` — лимиты всех аккаунтов\n"
            "• `/account cancel` — отменить вход"
        )
    except (CursorAccountError, CursorAccountLoginError) as exc:
        await message.answer(str(exc))
