"""Telegram inline keyboards."""

from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_cursor_agent.agent.session_format import (
    session_display_name,
    session_engine_name,
)
from telegram_cursor_agent.database.models.session import AgentSession


def mcp_setup_keyboard(confirmation_id: UUID) -> InlineKeyboardMarkup:
    cid = str(confirmation_id)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Установить", callback_data=f"mcp:install:{cid}"),
        InlineKeyboardButton(text="Отмена", callback_data=f"mcp:cancel:{cid}"),
    ]])


def confirmation_keyboard(confirmation_id: UUID) -> InlineKeyboardMarkup:
    cid = str(confirmation_id)
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Approve", callback_data=f"confirm:approve:{cid}"),
        InlineKeyboardButton(text="Reject", callback_data=f"confirm:reject:{cid}"),
    ]])


def memory_change_notify_keyboard(*, enabled: bool) -> InlineKeyboardMarkup:
    label = (
        "🔕 Выключить уведомления"
        if enabled
        else "🔔 Включить уведомления"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data="memory_notify:toggle")]
        ]
    )


def model_keyboard(models: list[dict[str, str]], page: int = 0) -> InlineKeyboardMarkup:
    page_size = 8
    start = page * page_size
    current = models[start : start + page_size]
    rows = [
        [InlineKeyboardButton(text=model["label"], callback_data=f"model:{model['id']}")]
        for model in current
    ]
    nav: list[InlineKeyboardButton] = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="‹", callback_data=f"models:{page - 1}"))
    if start + page_size < len(models):
        nav.append(InlineKeyboardButton(text="›", callback_data=f"models:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton(text="Обновить", callback_data="models:refresh")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sessions_menu_keyboard(sessions: list[AgentSession]) -> InlineKeyboardMarkup:
    """Titled session buttons plus new / delete / context actions."""
    from telegram_cursor_agent.telegram.main_menu import sessions_submenu_keyboard

    rows = list(session_resume_keyboard(sessions).inline_keyboard)
    for row in sessions_submenu_keyboard().inline_keyboard:
        if any(button.callback_data == "menu:act:resume" for button in row):
            continue
        rows.append(row)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def session_resume_keyboard(sessions: list[AgentSession]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, session in enumerate(sessions, start=1):
        marker = "● " if session.status == "active" else ""
        label = (
            f"{marker}{index}. {session_display_name(session)} · "
            f"{session_engine_name(session)}"
        )
        rows.append([
            InlineKeyboardButton(
                text=label[:64],
                callback_data=f"session:resume:{session.id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def session_delete_keyboard(sessions: list[AgentSession]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for index, session in enumerate(sessions, start=1):
        label = (
            f"{index}. {session_display_name(session)} · "
            f"{session_engine_name(session)}"
        )
        rows.append([
            InlineKeyboardButton(
                text=label[:64],
                callback_data=f"session:delete:{session.id}",
            )
        ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


