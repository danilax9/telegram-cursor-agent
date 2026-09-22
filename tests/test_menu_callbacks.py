"""Menu callback routing tests."""

from unittest.mock import MagicMock

from telegram_cursor_agent.telegram.handlers.callbacks import _is_general_menu_callback


def _cb(data: str) -> MagicMock:
    return MagicMock(data=data)


def test_general_menu_excludes_account_switch() -> None:
    assert _is_general_menu_callback(_cb("menu:acc:backup")) is False
    assert _is_general_menu_callback(_cb("menu:proj:00000000-0000-0000-0000-000000000001")) is False
    assert _is_general_menu_callback(_cb("menu:mcpadd:github")) is False


def test_general_menu_includes_hub_and_actions() -> None:
    assert _is_general_menu_callback(_cb("menu:home")) is True
    assert _is_general_menu_callback(_cb("menu:sub:cursor")) is True
    assert _is_general_menu_callback(_cb("menu:act:model")) is True
