"""Главное inline-меню — полное управление ботом без slash-команд."""

from __future__ import annotations

import uuid

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.models.project import Project
from telegram_cursor_agent.services.cursor_accounts import CursorAccount

MAIN_MENU_HEADER = "*Панель Cursor Agent*"

MAIN_MENU_TEXT = (
    f"{MAIN_MENU_HEADER}\n\n"
    "Все настройки и команды — кнопками ниже (уровни «Назад»).\n"
    "Задачи агенту — обычным сообщением в чат."
)

SESSIONS_SUBMENU_TEXT = (
    "*Сессии*\n\n"
    "Управление чатами Cursor: новая, переключение, удаление, сжатие контекста."
)

PROJECTS_SUBMENU_TEXT = (
    "*Проекты*\n\n"
    "Активный проект задаёт рабочую папку для git и агента."
)

GIT_SUBMENU_TEXT = (
    "*Git*\n\n"
    "Быстрые команды по *активному проекту*."
)

CURSOR_SUBMENU_TEXT = (
    "*Cursor*\n\n"
    "Модель, лимиты и окно контекста."
)

MCP_SUBMENU_TEXT = (
    "*MCP*\n\n"
    "Список и быстрая установка. Свой сервер — сообщением: "
    "«добавь mcp …» (секреты спросит в чате)."
)

TASKS_SUBMENU_TEXT = (
    "*Задачи и очередь*\n\n"
    "Статус, отмена и подтверждения."
)

HELP_SUBMENU_TEXT = (
    "*Справка*\n\n"
    "Разделы ниже — без отдельных сообщений, всё в этой панели."
)

OWNER_SUBMENU_TEXT = (
    "*Управление ботом*\n\n"
    "Только владелец: аккаунты Cursor, доступ пользователей, deploy."
)

DEPLOY_CONFIRM_TEXT = (
    "*Deploy*\n\n"
    "Переустановка пакета, миграции и перезапуск worker + bot. "
    "Текущие задачи могут прерваться.\n\n"
    "Продолжить?"
)

MCP_PRESETS: tuple[tuple[str, str], ...] = (
    ("GitHub", "github"),
    ("PostgreSQL", "postgres"),
    ("Figma", "figma"),
    ("Notion", "notion"),
    ("Slack", "slack"),
    ("Linear", "linear"),
)


def is_menu_owner(telegram_id: int, settings: Settings) -> bool:
    return telegram_id in settings.telegram_admin_ids


def _back_row(to: str = "menu:home") -> list[InlineKeyboardButton]:
    return [InlineKeyboardButton(text="← Назад", callback_data=to)]


def main_menu_keyboard(settings: Settings, telegram_id: int) -> InlineKeyboardMarkup:
    owner = is_menu_owner(telegram_id, settings)
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="📂 Сессии", callback_data="menu:sub:sessions"),
            InlineKeyboardButton(text="📁 Проект", callback_data="menu:sub:projects"),
        ],
        [
            InlineKeyboardButton(text="🔀 Git", callback_data="menu:sub:git"),
            InlineKeyboardButton(text="🧠 Cursor", callback_data="menu:sub:cursor"),
        ],
        [
            InlineKeyboardButton(text="🔌 MCP", callback_data="menu:sub:mcp"),
            InlineKeyboardButton(text="📋 Задачи", callback_data="menu:sub:tasks"),
        ],
        [
            InlineKeyboardButton(text="❓ Справка", callback_data="menu:sub:help"),
        ],
    ]
    if owner:
        rows.append(
            [
                InlineKeyboardButton(
                    text="👑 Управление", callback_data="menu:sub:owner"
                ),
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="menu:home"),
            InlineKeyboardButton(text="🛑 Отменить задачу", callback_data="task:cancel"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sessions_submenu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✨ Новая", callback_data="menu:act:new"),
                InlineKeyboardButton(
                    text="▶️ Продолжить", callback_data="menu:act:resume"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data="menu:act:delete"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗜 Сжать контекст",
                    callback_data="menu:act:summarize:sessions",
                ),
                InlineKeyboardButton(
                    text="📐 Контекст",
                    callback_data="menu:act:context:sessions",
                ),
            ],
            _back_row(),
        ]
    )


def git_submenu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📊 Status", callback_data="menu:act:git_status"
                ),
                InlineKeyboardButton(text="📄 Diff", callback_data="menu:act:git_diff"),
            ],
            [
                InlineKeyboardButton(
                    text="📜 Log", callback_data="menu:act:git_log"
                ),
            ],
            _back_row(),
        ]
    )


def cursor_submenu_keyboard(show_tool_calls_live: bool = False) -> InlineKeyboardMarkup:
    tool_toggle = (
        "🔧 Tool calls: вкл"
        if show_tool_calls_live
        else "🔧 Tool calls: выкл"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🧠 Модель", callback_data="menu:act:model"
                ),
                InlineKeyboardButton(
                    text="📊 Лимиты", callback_data="menu:act:limits"
                ),
            ],
            [
                InlineKeyboardButton(
                    text=tool_toggle,
                    callback_data="menu:act:toggle_tool_calls",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="📈 Все аккаунты",
                    callback_data="menu:act:account_limits",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🗜 Сжать",
                    callback_data="menu:act:summarize:cursor",
                ),
                InlineKeyboardButton(
                    text="📐 Контекст",
                    callback_data="menu:act:context:cursor",
                ),
            ],
            _back_row(),
        ]
    )


def mcp_submenu_keyboard() -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(
                text="📋 Список MCP", callback_data="menu:act:mcp_list"
            ),
        ],
    ]
    preset_row: list[InlineKeyboardButton] = []
    for label, slug in MCP_PRESETS:
        preset_row.append(
            InlineKeyboardButton(
                text=f"+ {label}",
                callback_data=f"menu:mcpadd:{slug}",
            )
        )
        if len(preset_row) == 2:
            rows.append(preset_row)
            preset_row = []
    if preset_row:
        rows.append(preset_row)
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def tasks_submenu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 Статус", callback_data="menu:act:status"
                ),
                InlineKeyboardButton(
                    text="🛑 Отменить", callback_data="task:cancel"
                ),
            ],
            _back_row(),
        ]
    )


def help_submenu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📖 Все команды", callback_data="menu:act:help"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💬 Как писать агенту",
                    callback_data="menu:act:help_agent",
                ),
                InlineKeyboardButton(
                    text="📁 Проекты",
                    callback_data="menu:act:help_projects",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔌 MCP",
                    callback_data="menu:act:help_mcp",
                ),
                InlineKeyboardButton(
                    text="👑 Админ",
                    callback_data="menu:act:help_admin",
                ),
            ],
            _back_row(),
        ]
    )


def owner_submenu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="👤 Аккаунты Cursor",
                    callback_data="menu:sub:accounts",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔐 Доступ",
                    callback_data="menu:act:access",
                ),
                InlineKeyboardButton(
                    text="➕ Выдать доступ",
                    callback_data="menu:act:access_add_hint",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚀 Deploy", callback_data="menu:sub:deploy"
                ),
            ],
            _back_row(),
        ]
    )


def deploy_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Запустить deploy",
                    callback_data="menu:act:deploy_go",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="← Управление", callback_data="menu:sub:owner"
                ),
            ],
        ]
    )


def projects_picker_keyboard(
    projects: list[Project], active_project_id: uuid.UUID | None
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for project in projects[:12]:
        marker = "✓ " if project.id == active_project_id else ""
        label = f"{marker}{project.name}"[:48]
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"menu:proj:{project.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔄 Обновить список",
                callback_data="menu:sub:projects",
            ),
        ]
    )
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def accounts_switch_keyboard(
    accounts: list[CursorAccount], active_id: str
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for account in accounts:
        marker = "✓ " if account.id == active_id else ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{marker}{account.label} ({account.id})"[:48],
                    callback_data=f"menu:acc:{account.id}",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="📈 Лимиты всех",
                callback_data="menu:act:account_limits",
            ),
            InlineKeyboardButton(
                text="➕ Добавить",
                callback_data="menu:act:account_add_hint",
            ),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(
                text="✖️ Отменить вход",
                callback_data="menu:act:account_cancel",
            ),
        ]
    )
    rows.append(_back_row("menu:sub:owner"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


STATIC_SUBMENUS: dict[str, tuple[str, object]] = {}


def _register_static() -> None:
    global STATIC_SUBMENUS
    STATIC_SUBMENUS.clear()
    STATIC_SUBMENUS.update(
        {
        "sessions": (SESSIONS_SUBMENU_TEXT, sessions_submenu_keyboard),
        "git": (GIT_SUBMENU_TEXT, git_submenu_keyboard),
        "cursor": (CURSOR_SUBMENU_TEXT, cursor_submenu_keyboard),
        "mcp": (MCP_SUBMENU_TEXT, mcp_submenu_keyboard),
        "tasks": (TASKS_SUBMENU_TEXT, tasks_submenu_keyboard),
        "help": (HELP_SUBMENU_TEXT, help_submenu_keyboard),
        "owner": (OWNER_SUBMENU_TEXT, owner_submenu_keyboard),
        "deploy": (DEPLOY_CONFIRM_TEXT, deploy_confirm_keyboard),
        }
    )


_register_static()


def static_submenu(submenu: str) -> tuple[str, InlineKeyboardMarkup] | None:
    entry = STATIC_SUBMENUS.get(submenu)
    if entry is None:
        return None
    text, kb_factory = entry
    assert callable(kb_factory)
    return text, kb_factory()
