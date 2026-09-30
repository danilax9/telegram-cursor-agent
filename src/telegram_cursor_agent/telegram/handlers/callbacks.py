"""Callback query handlers."""

import asyncio
from uuid import UUID

from aiogram import Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.session_format import session_display_name
from telegram_cursor_agent.agent.sessions import SessionService, engine_for_model
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.projects.service import ProjectService
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.access import AccessError, AccessService
from telegram_cursor_agent.services.actions import ActionService
from telegram_cursor_agent.services.confirmations import ConfirmationService
from telegram_cursor_agent.services.cursor_models import (
    CursorModelsError,
    load_models,
    load_selected_model_id,
    resolve_model_label,
    save_selected_model,
)
from telegram_cursor_agent.services.mcp_setup import McpSetupService
from telegram_cursor_agent.telegram.handlers.session_commands import handle_session_callback
from telegram_cursor_agent.telegram.keyboards import memory_change_notify_keyboard
from telegram_cursor_agent.telegram.menu_actions import (
    run_menu_action,
    start_mcp_preset,
    switch_cursor_account,
)
from telegram_cursor_agent.telegram.menu_input import clear_menu_input
from telegram_cursor_agent.telegram.main_menu import mcp_delete_confirm_keyboard
from telegram_cursor_agent.telegram.markdown import md_code_block
from telegram_cursor_agent.telegram.menu_navigation import (
    build_accounts_menu_view,
    build_home_view,
    build_mcp_server_view,
    build_mcp_view,
    build_submenu_view,
    select_project,
)
from telegram_cursor_agent.telegram.menu_screens import (
    menu_back_keyboard,
    merge_markup_with_back,
    prepare_menu_text,
)
from telegram_cursor_agent.telegram.model_keyboards import (
    ModelPickerState,
    model_picker_keyboard,
    model_picker_text,
    parse_mpick_callback,
    refresh_models_payload,
)

router = Router()

_MENU_DELEGATED_PREFIXES = ("menu:acc:", "menu:proj:", "menu:mcpadd:")


def _is_general_menu_callback(callback: CallbackQuery) -> bool:
    data = callback.data or ""
    if not data.startswith("menu:"):
        return False
    return not data.startswith(_MENU_DELEGATED_PREFIXES)


@router.callback_query(lambda c: c.data and c.data.startswith("session:"))
async def handle_session(
    callback: CallbackQuery,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    redis_client: Redis,  # type: ignore[type-arg]
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
    if action != "rename":
        await clear_menu_input(redis_client, telegram_user_id)
    await callback.answer()
    await handle_session_callback(
        action,
        session_id,
        db=db,
        settings=settings,
        telegram_user_id=telegram_user_id,
        message=callback.message,
        redis_client=redis_client,
    )


async def _render_model_picker(
    message: Message,
    settings: Settings,
    state: ModelPickerState,
) -> None:
    models = load_models(settings)
    await message.edit_text(
        model_picker_text(models, state, settings),
        reply_markup=model_picker_keyboard(models, state, settings),
    )


async def _queue_refresh_models(
    db: AsyncSession,
    task_queue: TaskQueue,
    user_id,
    *,
    payload: str,
) -> None:
    task = await TaskRepository(db).create(
        user_id=user_id,
        task_type="refresh_models",
        payload=payload,
    )
    await db.commit()
    await task_queue.enqueue(str(task.id))


@router.callback_query(lambda c: c.data and c.data.startswith("mpick:"))
async def handle_model_picker(
    callback: CallbackQuery,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
) -> None:
    parsed = parse_mpick_callback(callback.data or "")
    if parsed is None:
        await callback.answer("Некорректная кнопка")
        return
    prefix, state = parsed
    if prefix == "noop":
        await callback.answer()
        return

    if prefix == "ref":
        user = await UserRepository(db).get_by_telegram_id(telegram_user_id)
        if user is None:
            await callback.answer("Сначала отправь /start", show_alert=True)
            return
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        payload = refresh_models_payload(
            state,
            callback.message.chat.id,
            callback.message.message_id,
        )
        await _queue_refresh_models(db, task_queue, user.id, payload=payload)
        await callback.answer("Обновляю каталог…")
        loading_markup = (
            menu_back_keyboard("menu:home") if state.menu else None
        )
        await callback.message.edit_text(
            "*Модель Cursor*\n\nОбновляю каталог на сервере…",
            reply_markup=loading_markup,
        )
        return

    try:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        await _render_model_picker(callback.message, settings, state)
    except CursorModelsError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()


async def _session_switch_note(
    *,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    engine: str,
) -> str:
    user = await UserRepository(db).get_by_telegram_id(telegram_user_id)
    if user is None:
        return ""
    workspace = await ProjectService(db, settings).resolve_workspace(user)
    session, action = await SessionService(db, settings).align_to_engine(
        user.id,
        engine,
        workspace,
        user.active_project_id,
    )
    if action == "same":
        return ""
    label = "OpenCode" if engine == "opencode" else "Cursor"
    if action == "created":
        return f"\n\nНовая сессия {label}. Следующее сообщение начнёт её чат."
    title = session_display_name(session)
    return f"\n\nПродолжаю сессию {label}: *{title}*."


@router.callback_query(lambda c: c.data and c.data.startswith("model:"))
async def handle_model(
    callback: CallbackQuery,
    settings: Settings,
    db: AsyncSession,
    telegram_user_id: int,
) -> None:
    raw = callback.data.split(":", 1)[1] if callback.data else "auto"
    from_menu = raw.endswith(":m")
    model = raw[:-2] if from_menu else raw
    try:
        models = load_models(settings)
        label = next(
            (item["label"] for item in models if item.get("id") == model),
            model,
        )
    except CursorModelsError:
        label = model
    previous = load_selected_model_id(settings)
    await asyncio.to_thread(save_selected_model, settings, model)
    await callback.answer(f"✓ {label}")
    engine_key = engine_for_model(model)
    engine = "OpenCode" if engine_key == "opencode" else "Cursor"
    switch_note = ""
    if engine_for_model(previous) != engine_key:
        switch_note = await _session_switch_note(
            db=db,
            settings=settings,
            telegram_user_id=telegram_user_id,
            engine=engine_key,
        )
    if isinstance(callback.message, Message):
        if from_menu:
            try:
                models = load_models(settings)
                display = resolve_model_label(models, model)
            except CursorModelsError:
                display = label
            await callback.message.edit_text(
                f"*{engine}*\n\nМодель: *{display}*\n\n"
                f"_Новые сообщения пойдут с этой моделью._{switch_note}",
                reply_markup=menu_back_keyboard("menu:home"),
            )
            return
        await callback.message.edit_text(
            f"Модель {engine}: *{label}*{switch_note}"
        )


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
            await callback.message.edit_text(
                text, reply_markup=menu_back_keyboard("menu:sub:mcp")
            )
        return

    if action == "install":
        text = await service.install_from_confirmation(user.id, confirmation_id)
        await callback.answer("Готово")
        if isinstance(callback.message, Message):
            await callback.message.edit_text(
                text, reply_markup=menu_back_keyboard("menu:sub:mcp")
            )
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
    settings: Settings,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    telegram_user_id: int,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer("Ошибка")
        return
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
    if total:
        await callback.answer(f"Отменено задач: {total}")
    else:
        await callback.answer("Нет активных задач")
    result = await run_menu_action(
        "status",
        db=db,
        settings=settings,
        user=user,
        telegram_user_id=telegram_user_id,
        task_queue=task_queue,
        runner=runner,
        redis_client=None,
    )
    text = prepare_menu_text(result.text, settings.cursor_agent_max_output_bytes)
    markup = menu_back_keyboard(result.back_to or "menu:sub:tasks")
    if result.reply_markup is not None and result.back_to:
        markup = merge_markup_with_back(result.reply_markup, result.back_to)
    await callback.message.edit_text(text, reply_markup=markup)


async def _handle_mcp_menu(
    callback: CallbackQuery,
    payload: str,
    *,
    db: AsyncSession,
    settings: Settings,
    user_id: UUID,
    runner: ProcessRunner,
    task_queue: TaskQueue,
) -> None:
    assert isinstance(callback.message, Message)
    action, _, server_id = payload.partition(":")
    service = McpSetupService(db, settings, runner, task_queue)

    if action == "del":
        await callback.answer()
        await callback.message.edit_text(
            f"🗑 Удалить MCP `{server_id}`?\n\n"
            "Сервер пропадёт из конфига Cursor вместе с ключами.",
            reply_markup=mcp_delete_confirm_keyboard(server_id),
        )
        return

    if action == "delok":
        removed = service.remove_server(server_id)
        await callback.answer("Удалено" if removed else "Уже удалён")
        text, markup = await build_mcp_view(
            db=db,
            settings=settings,
            runner=runner,
            note=f"MCP `{server_id}` удалён." if removed else "",
        )
        await callback.message.edit_text(text, reply_markup=markup)
        return

    note = ""
    if action == "chk":
        await callback.answer("Проверяю…")
        result = await service.check_server(user_id, server_id)
        note = f"*Проверка:*\n{md_code_block(result)}"
    elif action == "srv":
        await callback.answer()
    else:
        await callback.answer("Неизвестная кнопка")
        return

    view = await build_mcp_server_view(
        server_id, db=db, settings=settings, runner=runner, note=note
    )
    if view is None:
        text, markup = await build_mcp_view(
            db=db,
            settings=settings,
            runner=runner,
            note=f"MCP `{server_id}` не найден.",
        )
    else:
        text, markup = view
    text = prepare_menu_text(text, settings.cursor_agent_max_output_bytes)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(_is_general_menu_callback)
async def handle_menu(
    callback: CallbackQuery,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    redis_client: Redis,  # type: ignore[type-arg]
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        await callback.answer("Некорректная кнопка")
        return

    users = UserRepository(db)
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        await callback.answer("Сначала /start", show_alert=True)
        return

    data = callback.data
    await clear_menu_input(redis_client, telegram_user_id)

    if data.startswith("menu:access:rm:"):
        target = data.removeprefix("menu:access:rm:")
        service = AccessService(db, settings)
        try:
            text = await service.revoke(telegram_user_id, target)
        except (PermissionError, AccessError) as exc:
            await callback.answer(str(exc), show_alert=True)
            return
        await callback.answer("Доступ убран")
        view = await build_submenu_view(
            "access",
            db=db,
            settings=settings,
            user=user,
            telegram_user_id=telegram_user_id,
            redis_client=redis_client,
        )
        if view is None:
            await callback.message.edit_text(text)
            return
        body, markup = view
        await callback.message.edit_text(f"{text}\n\n{body}", reply_markup=markup)
        return

    if data == "menu:home":
        await callback.answer()
        home_text, home_markup = await build_home_view(
            db=db,
            settings=settings,
            user=user,
            telegram_user_id=telegram_user_id,
        )
        await callback.message.edit_text(home_text, reply_markup=home_markup)
        return

    if data.startswith("menu:sub:"):
        submenu = data.removeprefix("menu:sub:")
        view = await build_submenu_view(
            submenu,
            db=db,
            settings=settings,
            user=user,
            telegram_user_id=telegram_user_id,
            redis_client=redis_client,
            runner=runner,
        )
        if view is None:
            await callback.answer("Раздел недоступен", show_alert=True)
            return
        text, markup = view
        await callback.answer()
        await callback.message.edit_text(text, reply_markup=markup)
        return

    if data.startswith("menu:mcp:"):
        await _handle_mcp_menu(
            callback,
            data.removeprefix("menu:mcp:"),
            db=db,
            settings=settings,
            user_id=user.id,
            runner=runner,
            task_queue=task_queue,
        )
        return

    if not data.startswith("menu:act:"):
        await callback.answer("Неизвестная кнопка")
        return

    payload = data.removeprefix("menu:act:")
    if payload == "refresh_models":
        refresh_state = ModelPickerState(menu=True)
        refresh_payload = refresh_models_payload(
            refresh_state,
            callback.message.chat.id,
            callback.message.message_id,
        )
        await _queue_refresh_models(
            db, task_queue, user.id, payload=refresh_payload
        )
        await callback.answer("Обновляю каталог…")
        await callback.message.edit_text(
            "*Модели*\n\nОбновляю каталог на сервере…",
            reply_markup=menu_back_keyboard("menu:home"),
        )
        return

    action = payload
    if payload.startswith("summarize:"):
        where = payload.split(":", 1)[1]
        action = "summarize_cursor" if where == "cursor" else "summarize"
    elif payload.startswith("context:"):
        where = payload.split(":", 1)[1]
        action = "context_cursor" if where == "cursor" else "context"

    await callback.answer()
    result = await run_menu_action(
        action,
        db=db,
        settings=settings,
        user=user,
        telegram_user_id=telegram_user_id,
        task_queue=task_queue,
        runner=runner,
        redis_client=redis_client,
        menu_message={
            "chat_id": callback.message.chat.id,
            "message_id": callback.message.message_id,
        },
    )
    text = prepare_menu_text(result.text, settings.cursor_agent_max_output_bytes)
    markup: InlineKeyboardMarkup | None = None
    if result.reply_markup is not None and result.back_to:
        markup = merge_markup_with_back(result.reply_markup, result.back_to)
    elif result.reply_markup is not None:
        markup = result.reply_markup
    elif result.back_to:
        markup = menu_back_keyboard(result.back_to)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(lambda c: c.data and c.data.startswith("menu:proj:"))
async def handle_menu_project(
    callback: CallbackQuery,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        await callback.answer("Ошибка")
        return
    users = UserRepository(db)
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        await callback.answer("Сначала /start", show_alert=True)
        return
    try:
        project_id = UUID(callback.data.removeprefix("menu:proj:"))
    except ValueError:
        await callback.answer("Неверный проект")
        return
    name = await select_project(
        project_id, db=db, settings=settings, user=user
    )
    if name is None:
        await callback.answer("Проект не найден", show_alert=True)
        return
    await callback.answer(f"Проект: {name}")
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        return
    view = await build_submenu_view(
        "projects",
        db=db,
        settings=settings,
        user=user,
        telegram_user_id=telegram_user_id,
    )
    if view is not None:
        await callback.message.edit_text(view[0], reply_markup=view[1])


@router.callback_query(lambda c: c.data and c.data.startswith("menu:acc:"))
async def handle_menu_account(
    callback: CallbackQuery,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    redis_client: Redis,  # type: ignore[type-arg]
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    user = await UserRepository(db).get_by_telegram_id(telegram_user_id)
    if user is None:
        await callback.answer("Сначала /start", show_alert=True)
        return
    account_id = callback.data.removeprefix("menu:acc:")
    menu_message = {
        "chat_id": callback.message.chat.id,
        "message_id": callback.message.message_id,
        "view": "accounts",
    }
    result = await switch_cursor_account(
        account_id,
        db=db,
        settings=settings,
        user=user,
        telegram_user_id=telegram_user_id,
        task_queue=task_queue,
        redis_client=redis_client,
        menu_message=menu_message,
    )
    short = result.text.replace("*", "").strip()
    toast = short[:120] if len(short) <= 120 else f"{short[:117]}…"
    await callback.answer(toast or "Готово")
    refreshed = await UserRepository(db).get_by_telegram_id(telegram_user_id)
    if refreshed is None:
        return
    if result.await_worker:
        base_text, markup = await build_accounts_menu_view(settings, redis_client)
        await callback.message.edit_text(
            f"{base_text}\n\n⏳ {result.text}",
            reply_markup=markup,
        )
        return
    if not result.refresh_accounts_menu:
        return
    view = await build_submenu_view(
        "accounts",
        db=db,
        settings=settings,
        user=refreshed,
        telegram_user_id=telegram_user_id,
        redis_client=redis_client,
    )
    if view is not None:
        await callback.message.edit_text(view[0], reply_markup=view[1])


@router.callback_query(lambda c: c.data and c.data.startswith("menu:mcpadd:"))
async def handle_menu_mcp_add(
    callback: CallbackQuery,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
) -> None:
    if not callback.data or not isinstance(callback.message, Message):
        return
    user = await UserRepository(db).get_by_telegram_id(telegram_user_id)
    if user is None:
        await callback.answer("Сначала /start", show_alert=True)
        return
    preset = callback.data.removeprefix("menu:mcpadd:")
    await callback.answer()
    result = await start_mcp_preset(
        preset,
        db=db,
        settings=settings,
        user=user,
        runner=runner,
        task_queue=task_queue,
    )
    text = prepare_menu_text(result.text, settings.cursor_agent_max_output_bytes)
    markup = result.reply_markup
    if markup is not None and result.back_to:
        markup = merge_markup_with_back(markup, result.back_to)
    elif result.back_to:
        markup = menu_back_keyboard(result.back_to)
    await callback.message.edit_text(text, reply_markup=markup)


@router.callback_query(lambda c: c.data == "memory_notify:toggle")
async def handle_memory_notify_toggle(
    callback: CallbackQuery,
    db: AsyncSession,
    telegram_user_id: int,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    users = UserRepository(db)
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        await callback.answer("Сначала /start", show_alert=True)
        return
    updated = await users.toggle_memory_change_notify(user.id)
    await db.commit()
    enabled = bool(updated.memory_change_notify) if updated is not None else True
    toast = (
        "Уведомления памяти включены"
        if enabled
        else "Уведомления памяти выключены"
    )
    await callback.answer(toast)
    try:
        await callback.message.edit_reply_markup(
            reply_markup=memory_change_notify_keyboard(enabled=enabled),
        )
    except TelegramBadRequest:
        pass
