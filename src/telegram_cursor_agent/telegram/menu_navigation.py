"""Сборка экранов меню (статических и динамических)."""

from __future__ import annotations

import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.session_format import (
    format_session_list,
    session_display_name,
)
from telegram_cursor_agent.agent.sessions import SessionService
from telegram_cursor_agent.agent.skills import discover_cursor_skills, format_skills_menu
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import is_super_admin
from telegram_cursor_agent.database.models.user import User
from telegram_cursor_agent.database.repositories.project import ProjectRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.projects.service import ProjectService
from telegram_cursor_agent.services.access import AccessService
from telegram_cursor_agent.services.cursor_account_login import CursorAccountLoginService
from telegram_cursor_agent.services.cursor_accounts import CursorAccountService
from telegram_cursor_agent.services.cursor_models import (
    CursorModelsError,
    load_models,
    load_selected_model_id,
    resolve_model_label,
)
from telegram_cursor_agent.services.mcp_setup import McpSetupService
from telegram_cursor_agent.telegram.keyboards import session_list_keyboard
from telegram_cursor_agent.telegram.main_menu import (
    CURSOR_SUBMENU_TEXT,
    LIVE_SUBMENU_TEXT,
    MAIN_MENU_HEADER,
    MCP_SUBMENU_TEXT,
    PROJECTS_SUBMENU_TEXT,
    SETTINGS_SUBMENU_TEXT,
    accounts_logged_out_keyboard,
    accounts_switch_keyboard,
    cursor_submenu_keyboard,
    is_menu_owner,
    live_settings_keyboard,
    main_menu_keyboard,
    mcp_server_keyboard,
    mcp_submenu_keyboard,
    projects_picker_keyboard,
    settings_submenu_keyboard,
    skills_submenu_keyboard,
    static_submenu,
)
from telegram_cursor_agent.telegram.model_keyboards import (
    initial_picker_state,
    model_picker_keyboard,
    model_picker_text,
)


async def build_accounts_menu_view(
    settings: Settings,
    redis_client: Redis | None,  # type: ignore[type-arg]
) -> tuple[str, InlineKeyboardMarkup]:
    service = CursorAccountService(settings, redis_client)
    accounts = service.list_accounts()
    logged_in = service.logged_in_accounts()
    if not logged_in:
        return (
            "*Аккаунты Cursor*\n\n"
            "Вход не выполнен. Зайди через Cursor, чтобы работать с его моделями.",
            accounts_logged_out_keyboard(),
        )
    active = await service.get_active_account()
    if not any(account.id == active.id for account in logged_in):
        active = logged_in[0]
    text = (
        "*Аккаунты Cursor*\n\n"
        f"Активный: *{active.label}*"
    )
    return text, accounts_switch_keyboard(accounts, active.id)


def build_live_settings_text(user: User) -> str:
    tools = "вкл" if user.show_tool_calls_live else "выкл"
    return (
        f"{LIVE_SUBMENU_TEXT}\n\n"
        f"Инструменты в процессе: *{tools}*\n"
        f"Review перед ответом: *{user.review_mode}*\n\n"
        "Live показывает вызовы tools и скиллы, пока задача идёт.\n"
        "Review: выкл, один проход или повтор, пока модель не подтвердит (max)."
    )


async def build_settings_view(
    *,
    settings: Settings,
    telegram_user_id: int,
    redis_client: Redis | None,  # type: ignore[type-arg]
) -> tuple[str, InlineKeyboardMarkup]:
    accounts = CursorAccountService(settings, redis_client)
    label = accounts.logged_in_label()
    pending = False
    if redis_client is not None:
        pending = await CursorAccountLoginService(
            settings, redis_client
        ).has_pending_login(telegram_user_id)
    owner = is_menu_owner(telegram_user_id, settings)
    return SETTINGS_SUBMENU_TEXT, settings_submenu_keyboard(
        logged_in=label is not None,
        login_pending=pending,
        owner=owner,
    )


def build_models_view(settings: Settings) -> tuple[str, InlineKeyboardMarkup]:
    accounts = CursorAccountService(settings, None)
    logged_in = accounts.logged_in_label() is not None
    try:
        models = load_models(settings)
    except CursorModelsError as exc:
        rows: list[list[InlineKeyboardButton]] = []
        if logged_in:
            rows.append([
                InlineKeyboardButton(
                    text="🔄 Обновить каталог",
                    callback_data="menu:act:refresh_models",
                )
            ])
        else:
            rows.append([
                InlineKeyboardButton(
                    text="🔑 Войти через Cursor", callback_data="menu:act:login"
                )
            ])
        rows.append([
            InlineKeyboardButton(text="← Назад", callback_data="menu:home")
        ])
        hint = (
            "Сначала войди в Cursor в настройках — после входа модели подгрузятся сами."
            if not logged_in
            else "Каталог пуст. Обнови его с сервера."
        )
        return (
            f"*Модели*\n\n{exc}\n\n{hint}",
            InlineKeyboardMarkup(inline_keyboard=rows),
        )
    state = initial_picker_state(models, settings, menu=True)
    return (
        model_picker_text(models, state, settings),
        model_picker_keyboard(models, state, settings),
    )


async def build_access_menu_view(
    *,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
) -> tuple[str, InlineKeyboardMarkup] | None:
    service = AccessService(db, settings)
    try:
        service.ensure_can_manage(telegram_user_id)
    except PermissionError:
        return None
    text = await service.list_access()
    granted = [
        user
        for user in await UserRepository(db).list_authorized()
        if not is_super_admin(user.telegram_id, settings)
    ]
    rows: list[list[InlineKeyboardButton]] = []
    for user in granted[:12]:
        label = f"@{user.username}" if user.username else str(user.telegram_id)
        rows.append([
            InlineKeyboardButton(
                text=f"🚫 Убрать {label}"[:48],
                callback_data=f"menu:access:rm:{user.telegram_id}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="➕ Выдать доступ",
            callback_data="menu:act:access_add",
        )
    ])
    rows.append([
        InlineKeyboardButton(text="← Назад", callback_data="menu:sub:settings")
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


async def build_mcp_view(
    *,
    db: AsyncSession,
    settings: Settings,
    runner: ProcessRunner | None = None,
    note: str = "",
) -> tuple[str, InlineKeyboardMarkup]:
    service = McpSetupService(db, settings, runner or ProcessRunner(settings))
    statuses = await service.server_statuses()
    lines = [MCP_SUBMENU_TEXT, ""]
    if note:
        lines.extend([note, ""])
    if statuses:
        lines.append("*Установлены:*")
        for server_id, status in statuses.items():
            lines.append(f"• `{server_id}` — {status or 'статус неизвестен'}")
        lines.append("")
        lines.append("Нажми сервер — проверить или удалить.")
    else:
        lines.append("Пока ни одного MCP.")
    lines.append("Добавить — кнопкой «+» или сообщением «добавь mcp …».")
    return "\n".join(lines), mcp_submenu_keyboard(statuses)


async def build_mcp_server_view(
    server_id: str,
    *,
    db: AsyncSession,
    settings: Settings,
    runner: ProcessRunner | None = None,
    note: str = "",
) -> tuple[str, InlineKeyboardMarkup] | None:
    service = McpSetupService(db, settings, runner or ProcessRunner(settings))
    statuses = await service.server_statuses()
    if server_id not in statuses:
        return None
    status = statuses[server_id] or "статус неизвестен"
    text = f"🔌 *{server_id}*\n\nСтатус: {status}"
    if note:
        text += f"\n\n{note}"
    return text, mcp_server_keyboard(server_id)


def build_cursor_submenu_text(settings: Settings) -> str:
    model_line = "_модель не выбрана_"
    try:
        models = load_models(settings)
        current = load_selected_model_id(settings)
        if current:
            model_line = f"*{resolve_model_label(models, current)}*"
    except CursorModelsError:
        model_line = "_каталог моделей недоступен_"
    return f"{CURSOR_SUBMENU_TEXT}\n\nТекущая модель: {model_line}"


async def build_home_view(
    *,
    db: AsyncSession,
    settings: Settings,
    user: User,
    telegram_user_id: int,
) -> tuple[str, InlineKeyboardMarkup]:
    lines = [
        MAIN_MENU_HEADER,
        "",
    ]
    if user.active_project_id:
        project = await ProjectRepository(db).get_by_id(user.active_project_id)
        if project is not None:
            lines.append(f"📁 *{project.name}* — `{project.root_path}`")
        else:
            lines.append("📁 _проект не найден_")
    else:
        project_service = ProjectService(db, settings)
        workspace = await project_service.resolve_workspace(user)
        lines.append(f"📁 Рабочая папка: `{workspace}`")

    session_service = SessionService(db, settings)
    active_session = await session_service.get_active(user.id)
    if active_session is not None:
        lines.append(f"💬 *{session_display_name(active_session)}*")
    else:
        lines.append("💬 _нет активной сессии_")

    lines.extend(
        [
            "",
            "Задачи агенту — сообщением. Остальное — кнопками (уровни «Назад»).",
        ]
    )
    return "\n".join(lines), main_menu_keyboard(settings, telegram_user_id)


async def build_submenu_view(
    submenu: str,
    *,
    db: AsyncSession,
    settings: Settings,
    user: User,
    telegram_user_id: int,
    redis_client: Redis | None = None,  # type: ignore[type-arg]
    runner: ProcessRunner | None = None,
) -> tuple[str, InlineKeyboardMarkup] | None:
    if submenu == "mcp":
        return await build_mcp_view(db=db, settings=settings, runner=runner)

    if submenu == "sessions":
        session_service = SessionService(db, settings)
        sessions = await session_service.list_resumable(user.id)
        active = await session_service.get_active(user.id)
        text = format_session_list(sessions, active.id if active else None)
        text += (
            "\n\nНажми сессию — откроются *Перейти*, *Удалить*, *Переименовать*. "
            "Чат сам не переключится."
        )
        return text, session_list_keyboard(sessions)

    if submenu == "models":
        return build_models_view(settings)

    if submenu == "settings":
        return await build_settings_view(
            settings=settings,
            telegram_user_id=telegram_user_id,
            redis_client=redis_client,
        )

    if submenu == "live":
        return (
            build_live_settings_text(user),
            live_settings_keyboard(
                user.show_tool_calls_live,
                review_mode=user.review_mode,
            ),
        )

    if submenu == "access":
        return await build_access_menu_view(
            db=db,
            settings=settings,
            telegram_user_id=telegram_user_id,
        )

    if submenu == "restart" and not is_menu_owner(telegram_user_id, settings):
        return None

    if submenu == "projects":
        project_service = ProjectService(db, settings)
        projects = await project_service.sync_discovered(user.id)
        active_id = user.active_project_id
        lines = [PROJECTS_SUBMENU_TEXT, ""]
        if not projects:
            lines.append("_Проекты не найдены. Проверь PROJECTS_ROOT на сервере._")
        else:
            for project in projects:
                mark = "✓ " if project.id == active_id else "• "
                lines.append(f"{mark}*{project.name}* — `{project.root_path}`")
        return "\n".join(lines), projects_picker_keyboard(projects, active_id)

    if submenu == "skills":
        workspace = await ProjectService(db, settings).resolve_workspace(user)
        skills = discover_cursor_skills(settings, workspace)
        return format_skills_menu(skills), skills_submenu_keyboard()

    if submenu == "cursor":
        return (
            build_cursor_submenu_text(settings),
            cursor_submenu_keyboard(
                user.show_tool_calls_live,
                review_mode=user.review_mode,
            ),
        )

    if submenu == "accounts":
        return await build_accounts_menu_view(settings, redis_client)

    static = static_submenu(submenu)
    if static is not None:
        if submenu == "owner" and telegram_user_id not in settings.telegram_admin_ids:
            return None
        if submenu == "deploy" and telegram_user_id not in settings.telegram_admin_ids:
            return None
        return static

    return None


async def select_project(
    project_id: uuid.UUID,
    *,
    db: AsyncSession,
    settings: Settings,
    user: User,
) -> str | None:
    from telegram_cursor_agent.database.repositories.project import ProjectRepository

    project = await ProjectRepository(db).get_by_id(project_id)
    if project is None:
        return None
    from telegram_cursor_agent.database.repositories.user import UserRepository

    await UserRepository(db).set_active_project(user.id, project.id)
    await db.commit()
    return project.name
