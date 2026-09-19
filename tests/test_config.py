"""Configuration tests."""

from pathlib import Path

from telegram_cursor_agent.core.config import Settings, clear_settings_cache, parse_path_list


def test_admin_ids_parsed_from_csv(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "111,222,333")
    clear_settings_cache()
    settings = Settings()
    assert settings.admin_telegram_ids == [111, 222, 333]


def test_discovery_roots_as_paths(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("PROJECT_SEARCH_ROOTS", "/tmp/a,/tmp/b")
    clear_settings_cache()
    settings = Settings()
    assert len(settings.project_search_roots) == 2
    assert all(isinstance(root, Path) for root in settings.project_search_roots)


def test_parse_path_list_from_csv_string() -> None:
    roots = parse_path_list("/tmp/a,/tmp/b")
    assert roots == [Path("/tmp/a"), Path("/tmp/b")]


def test_legacy_discovery_roots_alias(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("PROJECT_DISCOVERY_ROOTS", "/data/one,/data/two")
    clear_settings_cache()
    settings = Settings()
    assert settings.allowed_project_roots == [Path("/data/one"), Path("/data/two")]
    assert settings.project_search_roots == [Path("/data/one"), Path("/data/two")]


def test_single_admin_id(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "42")
    clear_settings_cache()
    settings = Settings()
    assert settings.admin_telegram_ids == [42]


def test_defaults_roots_from_projects_root(monkeypatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("PROJECTS_ROOT", "/srv/workspace")
    clear_settings_cache()
    settings = Settings()
    assert settings.projects_root == Path("/srv/workspace")
    assert settings.allowed_project_roots == [Path("/srv/workspace")]
    assert settings.project_search_roots == [Path("/srv/workspace")]
