"""Telegram session management commands."""

from uuid import UUID

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.session_format import (
    format_session_line,
    format_session_list,
    short_session_id,
)
from telegram_cursor_agent.agent.sessions import SessionError, SessionService
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import sanitize_for_telegram, split_telegram_message
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.projects.service import ProjectService
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.actions import ActionResultType, ActionService
from telegram_cursor_agent.telegram.session_live import (
    REDIRECT_STATUS_TEXT,
    edit_session_live_via_message,
)
from telegram_cursor_agent.telegram.keyboards import (
    session_delete_keyboard,
    session_resume_keyboard,
)
from telegram_cursor_agent.telegram.menu_screens import (
    menu_back_keyboard,
    prepare_menu_text,
)

router = Router()


async def _get_user(db: AsyncSession, telegram_user_id: int):
    users = UserRepository(db)
    return await users.get_by_telegram_id(telegram_user_id)


def _format_reply(text: str, settings: Settings) -> list[str]:
    safe = sanitize_for_telegram(text, settings.cursor_agent_max_output_bytes)
    return split_telegram_message(safe)


async def _send_reply(message: Message, text: str, settings: Settings, **kwargs) -> None:
    for chunk in _format_reply(text, settings):
        await message.answer(chunk, **kwargs)


async def _run_cursor_slash_command(
    message: Message,
    command: str,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    redis_client: Redis,  # type: ignore[type-arg]
) -> None:
    user = await _get_user(db, telegram_user_id)
    if user is None:
        await message.answer("Сначала отправь /start.")
        return

    project_service = ProjectService(db, settings)
    workspace = await project_service.resolve_workspace(user)
    sessions = SessionService(db, settings)
    agent_session = await sessions.get_active(user.id)
    action_service = ActionService(db, settings, runner, task_queue, redis_client)
    result = await action_service.queue_session_slash_command(
        user.id,
        command,
        workspace,
        project_id=user.active_project_id,
    )
    if result.result_type == ActionResultType.REDIRECT_REQUESTED:
        if agent_session is not None:
            await edit_session_live_via_message(
                redis_client,
                message,
                agent_session.id,
                REDIRECT_STATUS_TEXT,
                settings,
            )
        return
    if result.result_type == ActionResultType.TASK_QUEUED:
        return
    await message.answer(result.message)


@router.message(Command("summarize", "compact", "compress"))
async def cmd_summarize(
    message: Message,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    redis_client: Redis,  # type: ignore[type-arg]
) -> None:
    await _run_cursor_slash_command(
        message,
        "/summarize",
        db,
        settings,
        telegram_user_id,
        task_queue,
        runner,
        redis_client,
    )


@router.message(Command("deploy"))
async def cmd_deploy(
    message: Message,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
) -> None:
    user = await _get_user(db, telegram_user_id)
    if user is None:
        await message.answer("Сначала отправь /start.")
        return

    action_service = ActionService(db, settings, runner, task_queue)
    result = await action_service.queue_deploy(user.id)
    if result.result_type == ActionResultType.TASK_QUEUED:
        if result.message:
            await message.answer(result.message)
        return
    await message.answer(result.message)


@router.message(Command("context"))
async def cmd_context(
    message: Message,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    redis_client: Redis,  # type: ignore[type-arg]
) -> None:
    await _run_cursor_slash_command(
        message,
        "/context",
        db,
        settings,
        telegram_user_id,
        task_queue,
        runner,
        redis_client,
    )


@router.message(Command("new"))
async def cmd_new(
    message: Message,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
) -> None:
    user = await _get_user(db, telegram_user_id)
    if user is None:
        await message.answer("Сначала отправь /start.")
        return

    project_service = ProjectService(db, settings)
    workspace = await project_service.resolve_workspace(user)
    session_service = SessionService(db, settings)

    agent_session = await session_service.create_new(
        user.id,
        workspace,
        project_id=user.active_project_id,
    )

    text = (
        "*Новая сессия создана*\n\n"
        f"{format_session_line(agent_session, mark_active=True)}\n\n"
        "Контекст предыдущей сессии сохранён в архиве. "
        "Следующее сообщение начнёт новый чат Cursor без истории."
    )
    await _send_reply(message, text, settings)


@router.message(Command("resume"))
async def cmd_resume(
    message: Message,
    command: CommandObject,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
) -> None:
    user = await _get_user(db, telegram_user_id)
    if user is None:
        await message.answer("Сначала отправь /start.")
        return

    session_service = SessionService(db, settings)
    selector = (command.args or "").strip()

    if not selector:
        sessions = await session_service.list_resumable(user.id)
        active = await session_service.get_active(user.id)
        text = format_session_list(sessions, active.id if active else None)
        text += "\n\nВыбери сессию кнопкой ниже или отправь `/resume 1`."
        await _send_reply(
            message,
            text,
            settings,
            reply_markup=session_resume_keyboard(sessions),
        )
        return

    try:
        target = await session_service.resolve_selector(user.id, selector)
        activated = await session_service.activate(user.id, target.id)
    except SessionError as exc:
        await message.answer(str(exc))
        return

    text = (
        "*Сессия активирована*\n\n"
        f"{format_session_line(activated, mark_active=True)}\n\n"
        "Следующее сообщение продолжит этот чат Cursor."
    )
    await _send_reply(message, text, settings)


@router.message(Command("delete"))
async def cmd_delete(
    message: Message,
    command: CommandObject,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
) -> None:
    user = await _get_user(db, telegram_user_id)
    if user is None:
        await message.answer("Сначала отправь /start.")
        return

    session_service = SessionService(db, settings)
    selector = (command.args or "").strip()

    if not selector:
        active = await session_service.get_active(user.id)
        if active is not None:
            try:
                deleted = await session_service.delete(user.id, active.id)
            except SessionError as exc:
                await message.answer(str(exc))
                return
            text = (
                "*Текущая сессия удалена*\n\n"
                f"{format_session_line(deleted)}\n\n"
                "Следующее сообщение создаст новую сессию автоматически."
            )
            await _send_reply(message, text, settings)
            return

        sessions = await session_service.list_resumable(user.id)
        if not sessions:
            await message.answer("Нет сессий для удаления.")
            return
        text = (
            "*Активной сессии нет*\n\n"
            f"{format_session_list(sessions, None)}\n\n"
            "Выбери сессию для удаления кнопкой или отправь `/delete 1`."
        )
        await _send_reply(
            message,
            text,
            settings,
            reply_markup=session_delete_keyboard(sessions),
        )
        return

    try:
        target = await session_service.resolve_selector(user.id, selector)
        deleted = await session_service.delete(user.id, target.id)
    except SessionError as exc:
        await message.answer(str(exc))
        return

    text = (
        "*Сессия удалена*\n\n"
        f"{format_session_line(deleted)}\n\n"
        f"ID: `{short_session_id(deleted.id)}`"
    )
    await _send_reply(message, text, settings)


async def handle_session_callback(
    action: str,
    session_id: UUID,
    *,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    message: Message,
) -> None:
    user = await _get_user(db, telegram_user_id)
    if user is None:
        await message.answer("User not found")
        return

    session_service = SessionService(db, settings)
    try:
        if action == "resume":
            activated = await session_service.activate(user.id, session_id)
            text = (
                "*Сессия активирована*\n\n"
                f"{format_session_line(activated, mark_active=True)}\n\n"
                "Следующее сообщение продолжит этот чат Cursor."
            )
        elif action == "delete":
            deleted = await session_service.delete(user.id, session_id)
            text = (
                "*Сессия удалена*\n\n"
                f"{format_session_line(deleted)}\n\n"
                f"ID: `{short_session_id(deleted.id)}`"
            )
        else:
            await message.answer("Unknown action")
            return
    except SessionError as exc:
        if message.reply_markup is not None:
            await message.edit_text(
                str(exc),
                reply_markup=menu_back_keyboard("menu:sub:sessions"),
            )
        else:
            await message.answer(str(exc))
        return

    if message.reply_markup is not None:
        body = prepare_menu_text(text, settings.cursor_agent_max_output_bytes)
        await message.edit_text(
            body,
            reply_markup=menu_back_keyboard("menu:sub:sessions"),
        )
        return

    await _send_reply(message, text, settings)
