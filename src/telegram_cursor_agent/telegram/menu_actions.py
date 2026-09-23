"""Выполнение пунктов главного меню."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from aiogram.types import InlineKeyboardMarkup
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.prompts import HELP_TEXT
from telegram_cursor_agent.agent.session_format import (
    format_session_line,
    format_session_list,
)
from telegram_cursor_agent.agent.sessions import SessionError, SessionService
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import require_super_admin
from telegram_cursor_agent.database.models.user import User
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.telegram.main_menu import cursor_submenu_keyboard
from telegram_cursor_agent.telegram.menu_navigation import build_cursor_submenu_text
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.projects.service import ProjectService
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.access import AccessService
from telegram_cursor_agent.services.actions import ActionService
from telegram_cursor_agent.database.repositories.confirmation import ConfirmationRepository
from telegram_cursor_agent.services.cursor_account_login import CursorAccountLoginService
from telegram_cursor_agent.services.cursor_accounts import (
    CursorAccountService,
    CursorAccountError,
)
from telegram_cursor_agent.services.cursor_models import CursorModelsError, load_models
from telegram_cursor_agent.services.mcp_setup import McpSetupService
from telegram_cursor_agent.services.usage import CursorUsageError, format_usage_message
from telegram_cursor_agent.telegram.keyboards import (
    session_delete_keyboard,
    session_resume_keyboard,
)
from telegram_cursor_agent.telegram.menu_screens import mcp_setup_menu_keyboard
from telegram_cursor_agent.telegram.model_keyboards import (
    initial_picker_state,
    model_picker_keyboard,
    model_picker_text,
)

HELP_AGENT = (
    "*Как работать с агентом*\n\n"
    "• Пиши задачу обычным сообщением — запущу Cursor в активной сессии.\n"
    "• Фото + подпись или фото, потом текст — для картинок.\n"
    "• Пока задача идёт, новое сообщение *перенаправит* работу.\n"
    "• `run команда` — shell (опасные команды спросят подтверждение)."
)

HELP_PROJECTS = (
    "*Проекты*\n\n"
    "• Меню → *Проект* — выбор папки.\n"
    "• Текст: `projects`, `use project имя`.\n"
    "• Git-кнопки работают в активном проекте."
)

HELP_MCP = (
    "*MCP*\n\n"
    "• Меню → *MCP* — список и быстрая установка.\n"
    "• `/mcp add github` или «добавь mcp figma».\n"
    "• Секреты бот спросит в чате после поиска."
)

HELP_ADMIN = (
    "*Владелец*\n\n"
    "• *Аккаунты* — переключение Cursor, лимиты, вход через браузер.\n"
    "• *Доступ* — кто может пользоваться ботом.\n"
    "• *Deploy* — обновление кода и перезапуск.\n"
    "• `/access add TelegramID` — выдать доступ текстом."
)


@dataclass
class MenuActionResult:
    text: str
    reply_markup: InlineKeyboardMarkup | None = None
    back_to: str | None = None
    edit_menu: bool = True
    refresh_accounts_menu: bool = False
    await_worker: bool = False


async def run_menu_action(
    action: str,
    *,
    db: AsyncSession,
    settings: Settings,
    user: User,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    redis_client: Redis | None,  # type: ignore[type-arg]
) -> MenuActionResult:
    project_service = ProjectService(db, settings)
    workspace = await project_service.resolve_workspace(user)
    action_service = ActionService(
        db, settings, runner, task_queue, redis_client
    )
    session_service = SessionService(db, settings)
    login_service = CursorAccountLoginService(settings, redis_client)

    if action == "help":
        return MenuActionResult(HELP_TEXT, back_to="menu:sub:help")

    if action == "help_agent":
        return MenuActionResult(HELP_AGENT, back_to="menu:sub:help")

    if action == "help_projects":
        return MenuActionResult(HELP_PROJECTS, back_to="menu:sub:help")

    if action == "help_mcp":
        return MenuActionResult(HELP_MCP, back_to="menu:sub:help")

    if action == "help_admin":
        return MenuActionResult(HELP_ADMIN, back_to="menu:sub:help")

    if action == "status":
        tasks = TaskRepository(db)
        running = await tasks.list_running_for_user(user.id)
        pending_conf = await ConfirmationRepository(db).get_pending_for_user(user.id)
        lines = ["*Статус*", ""]
        if running:
            lines.append("*Выполняется:*")
            for task in running:
                lines.append(f"• `{task.task_type}` — с {task.started_at or task.created_at}")
        else:
            lines.append("Нет выполняющихся задач.")
        if pending_conf:
            lines.append("")
            lines.append(
                f"Ожидает подтверждения: *{pending_conf.action_type}*"
            )
        return MenuActionResult("\n".join(lines), back_to="menu:sub:tasks")

    if action in {"git_status", "git_diff", "git_log"}:
        intent_map = {
            "git_status": "git status",
            "git_diff": "git diff",
            "git_log": "git log",
        }
        result = await action_service.handle_text(
            user.id,
            intent_map[action],
            workspace,
            project_id=user.active_project_id,
        )
        return MenuActionResult(result.message, back_to="menu:sub:git")

    if action == "new":
        agent_session = await session_service.create_new(
            user.id,
            workspace,
            project_id=user.active_project_id,
        )
        text = (
            "*Новая сессия создана*\n\n"
            f"{format_session_line(agent_session, mark_active=True)}\n\n"
            "Следующее сообщение начнёт новый чат Cursor."
        )
        return MenuActionResult(text, back_to="menu:sub:sessions")

    if action == "resume":
        sessions = await session_service.list_resumable(user.id)
        active = await session_service.get_active(user.id)
        text = format_session_list(sessions, active.id if active else None)
        text += "\n\nВыбери сессию кнопкой ниже."
        return MenuActionResult(
            text,
            reply_markup=session_resume_keyboard(sessions),
            back_to="menu:sub:sessions",
        )

    if action == "delete":
        active = await session_service.get_active(user.id)
        if active is not None:
            try:
                deleted = await session_service.delete(user.id, active.id)
            except SessionError as exc:
                return MenuActionResult(str(exc), back_to="menu:sub:sessions")
            text = (
                "*Текущая сессия удалена*\n\n"
                f"{format_session_line(deleted)}\n\n"
                "Следующее сообщение создаст новую сессию."
            )
            return MenuActionResult(text, back_to="menu:sub:sessions")
        sessions = await session_service.list_resumable(user.id)
        if not sessions:
            return MenuActionResult(
                "Нет сессий для удаления.", back_to="menu:sub:sessions"
            )
        text = (
            "*Выбери сессию для удаления*\n\n"
            f"{format_session_list(sessions, None)}"
        )
        return MenuActionResult(
            text,
            reply_markup=session_delete_keyboard(sessions),
            back_to="menu:sub:sessions",
        )

    if action == "toggle_tool_calls":
        users = UserRepository(db)
        updated = await users.toggle_show_tool_calls_live(user.id)
        if updated is not None:
            user = updated
        await db.commit()
        return MenuActionResult(
            build_cursor_submenu_text(settings),
            reply_markup=cursor_submenu_keyboard(user.show_tool_calls_live),
            back_to="menu:sub:cursor",
        )

    if action == "model":
        try:
            models = load_models(settings)
        except CursorModelsError as exc:
            return MenuActionResult(str(exc), back_to="menu:sub:cursor")
        picker_state = initial_picker_state(models, settings, menu=True)
        return MenuActionResult(
            model_picker_text(models, picker_state, settings),
            reply_markup=model_picker_keyboard(models, picker_state, settings),
        )

    if action == "limits":
        accounts = CursorAccountService(settings, redis_client)
        try:
            active = await accounts.get_active_account()
            snapshot = await accounts.fetch_usage(active)
        except CursorUsageError as exc:
            return MenuActionResult(str(exc), back_to="menu:sub:cursor")
        except httpx.HTTPError:
            return MenuActionResult(
                "Не удалось получить лимиты Cursor. Попробуй позже.",
                back_to="menu:sub:cursor",
            )
        usage_text = format_usage_message(snapshot)
        return MenuActionResult(
            f"Аккаунт: `{active.id}` ({active.label})\n\n{usage_text}",
            back_to="menu:sub:cursor",
        )

    if action == "account_limits":
        try:
            require_super_admin(telegram_user_id, settings)
        except PermissionError:
            return MenuActionResult("Только владелец.", back_to="menu:sub:accounts")
        service = CursorAccountService(settings, redis_client)
        lines = ["*Лимиты всех аккаунтов*", ""]
        for account in service.list_accounts():
            try:
                snapshot = await service.fetch_usage(account)
                lines.append(f"*{account.id}* ({account.label})")
                lines.append(format_usage_message(snapshot))
                lines.append("")
            except CursorUsageError as exc:
                lines.append(f"*{account.id}*: {exc}")
                lines.append("")
        return MenuActionResult(
            "\n".join(lines).strip(), back_to="menu:sub:accounts"
        )

    if action == "account_add_hint":
        return MenuActionResult(
            "*Добавить аккаунт Cursor*\n\n"
            "Отправь сообщение:\n"
            "`/account add id`\n\n"
            "Например: `/account add backup`\n\n"
            "id — латиница, цифры, дефис, до 32 символов.\n"
            "Вход через браузер — бот пришлёт ссылку отдельным сообщением.",
            back_to="menu:sub:accounts",
        )

    if action == "account_cancel":
        try:
            require_super_admin(telegram_user_id, settings)
        except PermissionError:
            return MenuActionResult("Только владелец.", back_to="menu:sub:accounts")
        if login_service.is_cli_available():
            text = await login_service.cancel_login(telegram_user_id)
        else:
            text = await login_service.queue_login_cancel(
                db, task_queue, user.id, telegram_user_id
            )
        return MenuActionResult(text, back_to="menu:sub:accounts")

    if action == "summarize":
        result = await action_service.queue_session_slash_command(
            user.id,
            "/summarize",
            workspace,
            project_id=user.active_project_id,
        )
        if result.message:
            return MenuActionResult(result.message, back_to="menu:sub:sessions")
        return MenuActionResult(
            "Запускаю сжатие контекста…", back_to="menu:sub:sessions"
        )

    if action == "summarize_cursor":
        result = await action_service.queue_session_slash_command(
            user.id,
            "/summarize",
            workspace,
            project_id=user.active_project_id,
        )
        if result.message:
            return MenuActionResult(result.message, back_to="menu:sub:cursor")
        return MenuActionResult(
            "Запускаю сжатие контекста…", back_to="menu:sub:cursor"
        )

    if action == "context":
        result = await action_service.queue_session_slash_command(
            user.id,
            "/context",
            workspace,
            project_id=user.active_project_id,
        )
        if result.message:
            return MenuActionResult(result.message, back_to="menu:sub:sessions")
        return MenuActionResult(
            "Запрашиваю сводку контекста…", back_to="menu:sub:sessions"
        )

    if action == "context_cursor":
        result = await action_service.queue_session_slash_command(
            user.id,
            "/context",
            workspace,
            project_id=user.active_project_id,
        )
        if result.message:
            return MenuActionResult(result.message, back_to="menu:sub:cursor")
        return MenuActionResult(
            "Запрашиваю сводку контекста…", back_to="menu:sub:cursor"
        )

    if action == "mcp_list":
        service = McpSetupService(db, settings, runner, task_queue)
        return MenuActionResult(
            await service.list_servers(), back_to="menu:sub:mcp"
        )

    if action == "deploy_go":
        try:
            require_super_admin(telegram_user_id, settings)
        except PermissionError:
            return MenuActionResult(
                "Только владелец может запускать deploy.", back_to="menu:sub:deploy"
            )
        result = await action_service.queue_deploy(
            user.id,
            workspace=workspace,
            project_id=user.active_project_id,
        )
        if result.message:
            return MenuActionResult(result.message, back_to="menu:sub:owner")
        return MenuActionResult(
            "Deploy поставлен в очередь…", back_to="menu:sub:owner"
        )

    if action == "access":
        service = AccessService(db, settings)
        try:
            service.ensure_can_manage(telegram_user_id)
        except PermissionError:
            return MenuActionResult(
                "Только владелец смотрит список доступа.", back_to="menu:sub:owner"
            )
        return MenuActionResult(
            await service.list_access(), back_to="menu:sub:owner"
        )

    if action == "access_add_hint":
        service = AccessService(db, settings)
        try:
            service.ensure_can_manage(telegram_user_id)
        except PermissionError:
            return MenuActionResult(
                "Только владелец.", back_to="menu:sub:owner"
            )
        return MenuActionResult(
            "*Выдать доступ*\n\n"
            "Отправь сообщение:\n"
            "`/access add TelegramID`\n\n"
            "TelegramID — числовой id пользователя.",
            back_to="menu:sub:owner",
        )

    return MenuActionResult("Неизвестное действие меню.", back_to="menu:home")


async def start_mcp_preset(
    preset: str,
    *,
    db: AsyncSession,
    settings: Settings,
    user: User,
    runner: ProcessRunner,
    task_queue: TaskQueue,
) -> MenuActionResult:
    service = McpSetupService(db, settings, runner, task_queue)
    try:
        text, confirmation_id = await service.start_add(user.id, preset)
    except ValueError as exc:
        return MenuActionResult(str(exc))
    if confirmation_id is None:
        return MenuActionResult(text, back_to="menu:sub:mcp")
    return MenuActionResult(
        text,
        reply_markup=mcp_setup_menu_keyboard(confirmation_id),
        back_to="menu:sub:mcp",
    )


async def switch_cursor_account(
    account_id: str,
    *,
    db: AsyncSession,
    settings: Settings,
    user: User,
    telegram_user_id: int,
    task_queue: TaskQueue,
    redis_client: Redis | None,  # type: ignore[type-arg]
    menu_message: dict[str, int | str] | None = None,
) -> MenuActionResult:
    try:
        require_super_admin(telegram_user_id, settings)
    except PermissionError:
        return MenuActionResult("Только владелец.")
    service = CursorAccountService(settings, redis_client)
    try:
        service.get_account(account_id)
    except CursorAccountError as exc:
        return MenuActionResult(str(exc))
    login_service = CursorAccountLoginService(settings, redis_client)
    if not service.should_switch_on_worker():
        account = await service.set_active_account(account_id)
        return MenuActionResult(
            f"Активный аккаунт: *{account.label}* (`{account.id}`)",
            refresh_accounts_menu=True,
        )
    text = await login_service.queue_account_switch(
        db,
        task_queue,
        user.id,
        telegram_user_id,
        account_id,
        menu_message=menu_message,
    )
    return MenuActionResult(text, await_worker=True)
