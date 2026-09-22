"""Сборка экранов inline-меню (одно сообщение, только edit_text)."""

from __future__ import annotations

from uuid import UUID

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_cursor_agent.core.security import sanitize_for_telegram, split_telegram_message
from telegram_cursor_agent.telegram.keyboards import mcp_setup_keyboard

TELEGRAM_MENU_MAX = 3900


def menu_back_row(callback_data: str) -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="← Назад", callback_data=callback_data)]


def menu_back_keyboard(callback_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[menu_back_row(callback_data)])


def merge_markup_with_back(
    markup: InlineKeyboardMarkup,
    back_to: str,
) -> InlineKeyboardMarkup:
    rows = [list(row) for row in markup.inline_keyboard]
    back = menu_back_row(back_to)
    if rows and any(btn.callback_data == back_to for btn in rows[-1]):
        return InlineKeyboardMarkup(inline_keyboard=rows)
    if rows and rows[-1] and rows[-1][0].text.startswith("←"):
        rows[-1] = back
    else:
        rows.append(back)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def mcp_setup_menu_keyboard(confirmation_id: UUID) -> InlineKeyboardMarkup:
    rows = [list(row) for row in mcp_setup_keyboard(confirmation_id).inline_keyboard]
    rows.append(menu_back_row("menu:sub:mcp"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def prepare_menu_text(text: str, max_bytes: int) -> str:
    safe = sanitize_for_telegram(text, max_bytes)
    parts = split_telegram_message(safe, max_len=TELEGRAM_MENU_MAX)
    body = parts[0]
    if len(parts) > 1:
        body += (
            "\n\n_Показана часть вывода. Для git diff/log сузь изменения "
            "или смотри файл на сервере._"
        )
    return body
