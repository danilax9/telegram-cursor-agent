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
    "Сессии, модели, настройки, MCP и скиллы — кнопками ниже.\n"
    "Задачи агенту — обычным сообщением в чат."
)

SESSIONS_SUBMENU_TEXT = (
    "*Сессии*\n\n"
    "Нажми сессию — откроются действия: перейти, удалить, переименовать."
)

SETTINGS_SUBMENU_TEXT = "⚙️ *Настройки*"

LIVE_SUBMENU_TEXT = (
    "*Настройка live*\n\n"
    "Что показывать, пока задача идёт, и проверять ли ответ перед отправкой."
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

MCP_SUBMENU_TEXT = "🔌 *MCP*"

MCP_CALLBACK_MAX = 64

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
    "Только владелец: доступ пользователей и перезагрузка."
)

DEPLOY_CONFIRM_TEXT = (
    "*Перезагрузить*\n\n"
    "Обновление кода, миграции и перезапуск worker + bot. "
    "Текущая задача может прерваться.\n\n"
    "Продолжить?"
)

LOGOUT_CONFIRM_TEXT = "*Выйти из Cursor*\n\nВыйти?"

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
    del settings, telegram_id
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="📂 Сессии", callback_data="menu:sub:sessions"),
            InlineKeyboardButton(text="🧠 Модели", callback_data="menu:sub:models"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data="menu:sub:settings"),
            InlineKeyboardButton(text="🔌 MCP", callback_data="menu:sub:mcp"),
        ],
        [
            InlineKeyboardButton(text="📚 Скиллы", callback_data="menu:sub:skills"),
        ],
        [
            InlineKeyboardButton(text="🛑 Остановить задачу", callback_data="task:cancel"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def sessions_submenu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✨ Новая", callback_data="menu:act:new"),
            ],
            _back_row(),
        ]
    )


def settings_submenu_keyboard(
    *,
    logged_in: bool,
    login_pending: bool,
    owner: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="📊 Лимиты", callback_data="menu:act:limits"),
            InlineKeyboardButton(text="🔧 Настройка live", callback_data="menu:sub:live"),
        ],
    ]
    if owner:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🔐 Доступ к боту", callback_data="menu:sub:access"
                ),
                InlineKeyboardButton(
                    text="🔄 Перезагрузить", callback_data="menu:sub:restart"
                ),
            ]
        )
    if login_pending:
        account_row = [
            InlineKeyboardButton(
                text="✖️ Отменить вход", callback_data="menu:act:account_cancel"
            )
        ]
    elif logged_in:
        account_row = [
            InlineKeyboardButton(
                text="🚪 Выйти из Cursor", callback_data="menu:act:logout"
            )
        ]
    else:
        account_row = [
            InlineKeyboardButton(
                text="🔑 Войти через Cursor", callback_data="menu:act:login"
            )
        ]
    if owner:
        rows.append(account_row)
    rows.append(_back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def live_settings_keyboard(
    show_tool_calls_live: bool = False,
    *,
    review_mode: str = "off",
) -> InlineKeyboardMarkup:
    tool_toggle = (
        "🔧 Live: вкл"
        if show_tool_calls_live
        else "🔧 Live: выкл"
    )
    review_labels = {
        "on": "🔎 Review: вкл",
        "max": "🔎 Review: max",
    }
    review_toggle = review_labels.get(review_mode, "🔎 Review: выкл")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=tool_toggle,
                    callback_data="menu:act:toggle_tool_calls",
                ),
            ],
            [
                InlineKeyboardButton(
                    text=review_toggle,
                    callback_data="menu:act:toggle_review",
                ),
            ],
            _back_row("menu:sub:settings"),
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


def cursor_submenu_keyboard(
    show_tool_calls_live: bool = False,
    *,
    review_mode: str = "off",
) -> InlineKeyboardMarkup:
    tool_toggle = (
        "🔧 Live: tools + скиллы"
        if show_tool_calls_live
        else "🔧 Live: tools + скиллы (выкл)"
    )
    review_labels = {
        "on": "🔎 Review: вкл",
        "max": "🔎 Review: max",
    }
    review_toggle = review_labels.get(review_mode, "🔎 Review: выкл")
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
                    text=review_toggle,
                    callback_data="menu:act:toggle_review",
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


def _mcp_status_icon(status: str) -> str:
    lowered = status.lower()
    if lowered.startswith("ready") or lowered.startswith("connected"):
        return "✅"
    if not lowered:
        return "⚪️"
    return "⚠️"


def mcp_submenu_keyboard(
    statuses: dict[str, str] | None = None,
) -> InlineKeyboardMarkup:
    installed = statuses or {}
    rows: list[list[InlineKeyboardButton]] = []
    for server_id, status in installed.items():
        callback = f"menu:mcp:srv:{server_id}"
        if len(f"menu:mcp:delok:{server_id}") > MCP_CALLBACK_MAX:
            continue
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{_mcp_status_icon(status)} {server_id}"[:48],
                    callback_data=callback,
                )
            ]
        )
    preset_row: list[InlineKeyboardButton] = []
    for label, slug in MCP_PRESETS:
        if slug in installed:
            continue
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


def mcp_server_keyboard(server_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔄 Проверить", callback_data=f"menu:mcp:chk:{server_id}"
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить", callback_data=f"menu:mcp:del:{server_id}"
                ),
            ],
            _back_row("menu:sub:mcp"),
        ]
    )


def mcp_delete_confirm_keyboard(server_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Да, удалить", callback_data=f"menu:mcp:delok:{server_id}"
                ),
            ],
            _back_row(f"menu:mcp:srv:{server_id}"),
        ]
    )


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


def skills_submenu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_back_row()])


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
                    text="← Настройки", callback_data="menu:sub:settings"
                ),
            ],
        ]
    )


def logout_confirm_keyboard() -> InlineKeyboardMarkup:
    """Logout is destructive: require an explicit second tap."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🚪 Да, выйти",
                    callback_data="menu:act:logout_go",
                ),
                InlineKeyboardButton(
                    text="✖️ Отмена",
                    callback_data="menu:sub:settings",
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
    rows.append(_back_row("menu:sub:settings"))
    return InlineKeyboardMarkup(inline_keyboard=rows)


def accounts_switch_keyboard(
    accounts: list[CursorAccount], active_id: str
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    single = len(accounts) == 1
    for account in accounts:
        marker = "✓ " if account.id == active_id else ""
        text = f"{marker}{account.label}" if single else f"{marker}{account.label} ({account.id})"
        rows.append(
            [
                InlineKeyboardButton(
                    text=text[:48],
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


def accounts_logged_out_keyboard() -> InlineKeyboardMarkup:
    """No Cursor login on disk: offer login, not a switch list."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔑 Войти через Cursor",
                    callback_data="menu:act:login",
                )
            ],
            _back_row("menu:sub:owner"),
        ]
    )


STATIC_SUBMENUS: dict[str, tuple[str, object]] = {}


def _register_static() -> None:
    global STATIC_SUBMENUS
    STATIC_SUBMENUS.clear()
    STATIC_SUBMENUS.update(
        {
        "sessions": (SESSIONS_SUBMENU_TEXT, sessions_submenu_keyboard),
        "git": (GIT_SUBMENU_TEXT, git_submenu_keyboard),
        "restart": (DEPLOY_CONFIRM_TEXT, deploy_confirm_keyboard),
        "cursor": (CURSOR_SUBMENU_TEXT, cursor_submenu_keyboard),
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
