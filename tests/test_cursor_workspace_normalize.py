"""Cursor workspace normalization for --resume."""

from telegram_cursor_agent.core.config import Settings


def test_normalize_maps_container_and_project_root_to_agent_root(
    test_settings: Settings, monkeypatch,
) -> None:
    monkeypatch.setenv("SANDBOX_OPEN", "true")
    monkeypatch.setenv("AGENT_WORKSPACE", "/")
    monkeypatch.setenv("PROJECTS_ROOT", "/root/telegram-cursor-agent/workspace")
    from telegram_cursor_agent.core.config import clear_settings_cache, get_settings

    clear_settings_cache()
    settings = get_settings()
    assert settings.normalize_cursor_workspace("/workspace") == "/"
    assert settings.normalize_cursor_workspace("/root/telegram-cursor-agent/workspace") == "/"
    assert settings.normalize_cursor_workspace("/") == "/"
    assert settings.normalize_cursor_workspace("/opt/my-app") == "/opt/my-app"
