"""Main inline menu tests."""

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.telegram.main_menu import (
    MAIN_MENU_TEXT,
    is_menu_owner,
    main_menu_keyboard,
    mcp_submenu_keyboard,
    static_submenu,
)


def test_main_menu_hub_text() -> None:
    assert "Панель" in MAIN_MENU_TEXT


def test_owner_sees_management(test_settings: Settings) -> None:
    owner_id = test_settings.telegram_admin_ids[0]
    kb = main_menu_keyboard(test_settings, owner_id)
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "menu:sub:owner" in flat
    assert "menu:sub:sessions" in flat
    assert "menu:sub:git" in flat


def test_non_owner_hides_management(test_settings: Settings) -> None:
    kb = main_menu_keyboard(test_settings, 999_999_999)
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "menu:sub:owner" not in flat


def test_mcp_presets_fit_callback_data() -> None:
    kb = mcp_submenu_keyboard()
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data and btn.callback_data.startswith("menu:mcpadd:"):
                assert len(btn.callback_data) <= 64


def test_static_submenu_back_to_home() -> None:
    view = static_submenu("git")
    assert view is not None
    text, kb = view
    assert "Git" in text
    back = kb.inline_keyboard[-1][0].callback_data
    assert back == "menu:home"


def test_is_menu_owner(test_settings: Settings) -> None:
    assert is_menu_owner(test_settings.telegram_admin_ids[0], test_settings)
    assert not is_menu_owner(1, test_settings)
