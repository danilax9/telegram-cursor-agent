"""Callback query handlers."""

import asyncio
import json
from uuid import UUID

from aiogram import Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.prompts import HELP_TEXT
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.projects.service import ProjectService
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.actions import ActionService
from telegram_cursor_agent.services.confirmations import ConfirmationService
from telegram_cursor_agent.telegram.handlers.session_commands import handle_session_callback
from telegram_cursor_agent.telegram.keyboards import model_keyboard

router = Router()


@router.callback_query(lambda c: c.data and c.data.startswith("session:"))
async def handle_session(
    callback: CallbackQuery,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        await callback.answer("Invalid callback")
        return
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Invalid callback")
        return
    _, action, session_id_str = parts
    try:
        session_id = UUID(session_id_str)
    except ValueError:
        await callback.answer("Invalid session id")
        return
    await callback.answer()
    await handle_session_callback(
        action,
        session_id,
        db=db,
        settings=settings,
        telegram_user_id=telegram_user_id,
        message=callback.message,
    )


@router.callback_query(lambda c: c.data and c.data.startswith("models:"))
async def handle_models_page(callback: CallbackQuery, settings: Settings) -> None:
    value = callback.data.split(":", 1)[1] if callback.data else "0"
    if value == "refresh":
        await callback.answer("каталог обновляется на сервере")
        return
    page = int(value)
    models_path = settings.projects_root / "cursor-models.json"
    raw = await asyncio.to_thread(models_path.read_text)
    models = json.loads(raw)
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(reply_markup=model_keyboard(models, page))


@router.callback_query(lambda c: c.data and c.data.startswith("model:"))
async def handle_model(callback: CallbackQuery, settings: Settings) -> None:
    model = callback.data.split(":", 1)[1] if callback.data else "auto"
    model_file = settings.projects_root / ".cursor_model"
    await asyncio.to_thread(model_file.write_text, model + "\n")
    await callback.answer("модель выбрана")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(f"модель Cursor: `{model}`")


@router.callback_query(lambda c: c.data and c.data.startswith("confirm:"))
async def handle_confirmation(
    callback: CallbackQuery,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
) -> None:
    if not callback.data:
        return
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Invalid callback")
        return

    action, confirmation_id_str = parts[1], parts[2]
    confirmation_id = UUID(confirmation_id_str)
    users = UserRepository(db)
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        await callback.answer("User not found")
        return

    confirm_service = ConfirmationService(db, settings)

    if action == "reject":
        await confirm_service.reject(confirmation_id, user.id)
        await callback.answer("Rejected")
        if isinstance(callback.message, Message):
            await callback.message.edit_text("Action rejected.")
        return

    if action == "approve":
        confirmation = await confirm_service.approve(confirmation_id, user.id)
        if confirmation is None:
            await callback.answer("Confirmation expired", show_alert=True)
            return

        action_service = ActionService(db, settings, runner, task_queue)
        result = await action_service.execute_confirmed_action(user.id, confirmation_id)
        await callback.answer("Approved")
        if isinstance(callback.message, Message):
            await callback.message.edit_text(result.message)
        return

    await callback.answer("Unknown action")


@router.callback_query(lambda c: c.data == "task:cancel")
async def handle_cancel_task(
    callback: CallbackQuery,
    db: AsyncSession,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    telegram_user_id: int,
) -> None:
    users = UserRepository(db)
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        await callback.answer("User not found")
        return
    tasks = TaskRepository(db)
    running = await tasks.list_running_for_user(user.id)
    local_cancelled = await runner.cancel_all()
    for task in running:
        await task_queue.publish_cancel(str(task.id))
        await tasks.mark_cancelled(task.id)
    total = local_cancelled + len(running)
    await callback.answer(f"Cancelled {total} process(es)")


@router.callback_query(lambda c: c.data and c.data.startswith("menu:"))
async def handle_menu(
    callback: CallbackQuery,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
) -> None:
    if not callback.data:
        return
    menu_action = callback.data.split(":", 1)[1]
    users = UserRepository(db)
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        await callback.answer("User not found")
        return

    project_service = ProjectService(db, settings)
    workspace = await project_service.resolve_workspace(user)
    action_service = ActionService(db, settings, runner, task_queue)
    text_map = {
        "help": "help",
        "status": "status",
        "projects": "projects",
    }
    text = text_map.get(menu_action, "help")
    result = await action_service.handle_text(
        user.id, text, workspace, project_id=user.active_project_id
    )
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(result.message if menu_action != "help" else HELP_TEXT)
