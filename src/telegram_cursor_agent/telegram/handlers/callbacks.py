"""Callback query handlers."""

import asyncio
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
from telegram_cursor_agent.services.cursor_model_catalog import (
    build_model_catalog,
    find_base,
    find_family,
)
from telegram_cursor_agent.services.cursor_models import (
    CursorModelsError,
    load_models,
    save_selected_model,
)
from telegram_cursor_agent.services.mcp_setup import McpSetupService
from telegram_cursor_agent.telegram.handlers.session_commands import handle_session_callback
from telegram_cursor_agent.telegram.model_keyboards import (
    model_bases_keyboard,
    model_families_keyboard,
    model_selection_text,
    model_variants_keyboard,
)

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
async def handle_models_refresh(
    callback: CallbackQuery,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
) -> None:
    value = callback.data.split(":", 1)[1] if callback.data else ""
    if value != "refresh":
        await callback.answer("устаревшая кнопка")
        return

    # Only the host worker has the Cursor CLI, so the refresh runs as a task.
    user = await UserRepository(db).get_by_telegram_id(telegram_user_id)
    if user is None:
        await callback.answer("Сначала отправь /start", show_alert=True)
        return
    task = await TaskRepository(db).create(
        user_id=user.id,
        task_type="refresh_models",
        payload="{}",
    )
    await db.commit()
    await task_queue.enqueue(str(task.id))
    await callback.answer("Обновляю каталог моделей…")


@router.callback_query(lambda c: c.data and c.data.startswith("mfams:"))
async def handle_model_families_page(callback: CallbackQuery, settings: Settings) -> None:
    page = int(callback.data.split(":", 1)[1] if callback.data else "0")
    try:
        models = load_models(settings)
    except CursorModelsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            model_selection_text(models),
            reply_markup=model_families_keyboard(models, page),
        )


@router.callback_query(lambda c: c.data and c.data.startswith("mfam:"))
async def handle_model_family(callback: CallbackQuery, settings: Settings) -> None:
    family_key = callback.data.split(":", 1)[1] if callback.data else ""
    try:
        models = load_models(settings)
        family = find_family(build_model_catalog(models), family_key)
        if family is None:
            raise CursorModelsError("Семейство моделей не найдено.")
    except CursorModelsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            model_selection_text(models, family_key=family_key),
            reply_markup=model_bases_keyboard(models, family_key),
        )


@router.callback_query(lambda c: c.data and c.data.startswith("mbases:"))
async def handle_model_bases_page(callback: CallbackQuery, settings: Settings) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 3:
        await callback.answer("Invalid callback")
        return
    family_key, page_str = parts[1], parts[2]
    page = int(page_str)
    try:
        models = load_models(settings)
    except CursorModelsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            model_selection_text(models, family_key=family_key),
            reply_markup=model_bases_keyboard(models, family_key, page),
        )


@router.callback_query(lambda c: c.data and c.data.startswith("mbase:"))
async def handle_model_base(callback: CallbackQuery, settings: Settings) -> None:
    parts = (callback.data or "").split(":", 2)
    if len(parts) != 3:
        await callback.answer("Invalid callback")
        return
    family_key, base_key = parts[1], parts[2]
    try:
        models = load_models(settings)
        catalog = build_model_catalog(models)
        family = find_family(catalog, family_key)
        if family is None:
            raise CursorModelsError("Семейство моделей не найдено.")
        base = find_base(family, base_key)
        if base is None:
            raise CursorModelsError("Модель не найдена.")
    except CursorModelsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            model_selection_text(models, family_key=family_key, base=base),
            reply_markup=model_variants_keyboard(models, family_key, base_key),
        )


@router.callback_query(lambda c: c.data and c.data.startswith("mvars:"))
async def handle_model_variants_page(callback: CallbackQuery, settings: Settings) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) != 4:
        await callback.answer("Invalid callback")
        return
    family_key, base_key, page_str = parts[1], parts[2], parts[3]
    page = int(page_str)
    try:
        models = load_models(settings)
        catalog = build_model_catalog(models)
        family = find_family(catalog, family_key)
        base = find_base(family, base_key) if family is not None else None
        if family is None or base is None:
            raise CursorModelsError("Модель не найдена.")
    except CursorModelsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            model_selection_text(models, family_key=family_key, base=base),
            reply_markup=model_variants_keyboard(models, family_key, base_key, page),
        )


@router.callback_query(lambda c: c.data and c.data.startswith("model:"))
async def handle_model(callback: CallbackQuery, settings: Settings) -> None:
    model = callback.data.split(":", 1)[1] if callback.data else "auto"
    try:
        models = load_models(settings)
        label = next(
            (item["label"] for item in models if item.get("id") == model),
            model,
        )
    except CursorModelsError:
        label = model
    await asyncio.to_thread(save_selected_model, settings, model)
    await callback.answer("модель выбрана")
    if isinstance(callback.message, Message):
        await callback.message.edit_text(f"Модель Cursor: *{label}*")


@router.callback_query(lambda c: c.data and c.data.startswith("mcp:"))
async def handle_mcp_setup(
    callback: CallbackQuery,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    runner: ProcessRunner,
    task_queue: TaskQueue,
) -> None:
    if not callback.data:
        return
    parts = callback.data.split(":", 2)
    if len(parts) != 3:
        await callback.answer("Invalid callback")
        return

    action, confirmation_id_str = parts[1], parts[2]
    try:
        confirmation_id = UUID(confirmation_id_str)
    except ValueError:
        await callback.answer("Invalid id")
        return

    users = UserRepository(db)
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        await callback.answer("User not found")
        return

    service = McpSetupService(db, settings, runner, task_queue)
    if action == "cancel":
        text = await service.cancel(user.id, confirmation_id)
        await callback.answer("Отменено")
        if isinstance(callback.message, Message):
            await callback.message.edit_text(text)
        return

    if action == "install":
        text = await service.install_from_confirmation(user.id, confirmation_id)
        await callback.answer("Готово")
        if isinstance(callback.message, Message):
            await callback.message.edit_text(text)
        return

    await callback.answer("Unknown action")


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
