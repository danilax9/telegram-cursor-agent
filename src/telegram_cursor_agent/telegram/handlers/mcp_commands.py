"""Telegram /mcp management commands."""

from uuid import UUID

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.mcp_setup import McpSetupService
from telegram_cursor_agent.telegram.keyboards import mcp_setup_keyboard

router = Router()


@router.message(Command("mcp"))
async def cmd_mcp(
    message: Message,
    command: CommandObject,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    runner: ProcessRunner,
    task_queue: TaskQueue,
) -> None:
    user = await UserRepository(db).get_by_telegram_id(telegram_user_id)
    if user is None:
        await message.answer("Сначала отправь /start.")
        return

    service = McpSetupService(db, settings, runner, task_queue)
    args = (command.args or "").strip()

    if not args or args.lower() in {"list", "ls", "status"}:
        await message.answer(await service.list_servers())
        return

    if args.lower().startswith("add "):
        query = args[4:].strip()
        if not query:
            await message.answer("Укажи MCP: `/mcp add github`")
            return
        text, confirmation_id = await service.start_add(user.id, query)
        if confirmation_id is None:
            await message.answer(text)
            return
        await message.answer(text, reply_markup=mcp_setup_keyboard(confirmation_id))
        return

    await message.answer(
        "Команды MCP:\n"
        "• `/mcp` — список\n"
        "• `/mcp add github` — найти и подключить\n"
        "• или напиши: `добавь mcp figma`"
    )
