"""Сборка экранов меню (статических и динамических)."""

from __future__ import annotations

import uuid

from aiogram.types import InlineKeyboardMarkup
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.models.user import User
from telegram_cursor_agent.projects.service import ProjectService
from telegram_cursor_agent.services.cursor_accounts import CursorAccountService
from telegram_cursor_agent.agent.session_format import workspace_label
from telegram_cursor_agent.agent.sessions import SessionService
from telegram_cursor_agent.database.repositories.project import ProjectRepository
from telegram_cursor_agent.services.cursor_models import (
    CursorModelsError,
    load_models,
    load_selected_model_id,
    resolve_model_label,
)
from telegram_cursor_agent.telegram.main_menu import (
    CURSOR_SUBMENU_TEXT,
    MAIN_MENU_HEADER,
    PROJECTS_SUBMENU_TEXT,
    accounts_switch_keyboard,
    cursor_submenu_keyboard,
    main_menu_keyboard,
    projects_picker_keyboard,
    static_submenu,
)


async def build_accounts_menu_view(
    settings: Settings,
    redis_client: Redis | None,  # type: ignore[type-arg]
) -> tuple[str, InlineKeyboardMarkup]:
    service = CursorAccountService(settings, redis_client)
    active = await service.get_active_account()
    accounts = service.list_accounts()
    text = (
        "*Аккаунты Cursor*\n\n"
        f"Активный: `{active.id}` ({active.label})\n\n"
        "Нажми аккаунт для переключения."
    )
    return text, accounts_switch_keyboard(accounts, active.id)


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
        lines.append(
            f"💬 Сессия `{active_session.id}` — "
            f"{workspace_label(active_session.workspace_path)}"
        )
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
) -> tuple[str, InlineKeyboardMarkup] | None:
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

    if submenu == "cursor":
        return build_cursor_submenu_text(settings), cursor_submenu_keyboard()

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
