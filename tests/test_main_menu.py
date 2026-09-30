"""Main inline menu tests."""

import pytest

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
    assert "menu:sub:sessions" in flat
    assert "menu:sub:models" in flat
    assert "menu:sub:settings" in flat
    assert "menu:sub:skills" in flat
    assert "menu:sub:mcp" in flat
    assert "task:cancel" in flat
    assert "menu:sub:git" not in flat
    assert "menu:sub:owner" not in flat


def test_settings_owner_has_access_and_login(test_settings: Settings) -> None:
    from telegram_cursor_agent.telegram.main_menu import settings_submenu_keyboard

    owner = settings_submenu_keyboard(
        logged_in=True, login_pending=False, owner=True
    )
    flat = [btn.callback_data for row in owner.inline_keyboard for btn in row]
    assert "menu:act:limits" in flat
    assert "menu:sub:live" in flat
    assert "menu:sub:access" in flat
    assert "menu:sub:restart" in flat
    assert "menu:act:logout" in flat

    guest = settings_submenu_keyboard(
        logged_in=False, login_pending=False, owner=False
    )
    guest_flat = [btn.callback_data for row in guest.inline_keyboard for btn in row]
    assert "menu:sub:access" not in guest_flat
    assert "menu:act:login" not in guest_flat


def test_session_tap_opens_actions() -> None:
    from uuid import uuid4

    from telegram_cursor_agent.telegram.keyboards import session_actions_keyboard

    session_id = uuid4()
    kb = session_actions_keyboard(session_id)
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert f"session:resume:{session_id}" in flat
    assert f"session:delete:{session_id}" in flat
    assert f"session:rename:{session_id}" in flat
    assert all(item is not None and len(item) <= 64 for item in flat)


def test_non_owner_hides_management(test_settings: Settings) -> None:
    kb = main_menu_keyboard(test_settings, 999_999_999)
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "menu:sub:owner" not in flat
    assert "menu:sub:settings" in flat


def test_mcp_presets_fit_callback_data() -> None:
    kb = mcp_submenu_keyboard()
    for row in kb.inline_keyboard:
        for btn in row:
            if btn.callback_data and btn.callback_data.startswith("menu:mcpadd:"):
                assert len(btn.callback_data) <= 64


def test_mcp_menu_lists_installed_and_hides_their_presets() -> None:
    kb = mcp_submenu_keyboard({"github": "ready"})
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "menu:mcp:srv:github" in flat
    assert "menu:mcpadd:github" not in flat
    assert "menu:mcpadd:postgres" in flat
    assert "menu:act:mcp_list" not in flat


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


def test_accounts_logged_out_keyboard_offers_login() -> None:
    from telegram_cursor_agent.telegram.main_menu import accounts_logged_out_keyboard

    kb = accounts_logged_out_keyboard()
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "menu:act:login" in flat
    assert "menu:sub:owner" in flat
    assert not any(cb.startswith("menu:acc:") for cb in flat)


@pytest.mark.asyncio
async def test_accounts_menu_reports_logged_out_state(test_settings: Settings) -> None:
    from telegram_cursor_agent.telegram.menu_navigation import build_accounts_menu_view

    text, kb = await build_accounts_menu_view(test_settings, None)
    flat = [btn.callback_data for row in kb.inline_keyboard for btn in row]
    assert "Вход не выполнен" in text
    assert "menu:act:login" in flat
    assert not any(cb.startswith("menu:acc:") for cb in flat)


def test_logout_requires_confirmation() -> None:
    """Tapping logout must only ask, not sign out immediately."""
    from telegram_cursor_agent.telegram.main_menu import (
        LOGOUT_CONFIRM_TEXT,
        logout_confirm_keyboard,
    )

    flat = [
        btn.callback_data
        for row in logout_confirm_keyboard().inline_keyboard
        for btn in row
    ]
    assert "menu:act:logout_go" in flat
    assert "menu:act:logout" not in flat
    assert "menu:sub:settings" in flat
    assert LOGOUT_CONFIRM_TEXT.strip().endswith("Выйти?")


def test_logout_confirm_keyboard_has_no_back_arrow() -> None:
    """Confirm dialog lives in place; it needs no extra back row."""
    from telegram_cursor_agent.telegram.main_menu import logout_confirm_keyboard

    texts = [
        btn.text
        for row in logout_confirm_keyboard().inline_keyboard
        for btn in row
    ]
    assert not any(text.startswith("←") for text in texts)
