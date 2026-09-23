"""Cursor model catalog path resolution tests."""

import json

import pytest

from telegram_cursor_agent.core.config import clear_settings_cache
from telegram_cursor_agent.services.cursor_models import (
    CursorModelsError,
    catalog_from_models_output,
    effective_projects_root,
    load_models,
    parse_models_output,
    resolve_model_file,
    save_selected_model,
    write_models_catalog,
)


@pytest.fixture
def host_projects_settings(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    clear_settings_cache()
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "12345")
    monkeypatch.setenv("PROJECTS_ROOT", str(workspace))
    clear_settings_cache()
    from telegram_cursor_agent.core.config import get_settings

    return get_settings(), workspace


def test_effective_projects_root_uses_existing_path(host_projects_settings) -> None:
    settings, workspace = host_projects_settings
    assert effective_projects_root(settings) == workspace
    assert settings.effective_projects_root == workspace


def test_load_models_reads_catalog(host_projects_settings) -> None:
    settings, workspace = host_projects_settings
    (workspace / "cursor-models.json").write_text(
        json.dumps([{"id": "auto", "label": "Auto"}]),
        encoding="utf-8",
    )
    models = load_models(settings)
    assert models[0]["id"] == "auto"


def test_load_models_missing_file(host_projects_settings) -> None:
    settings, _workspace = host_projects_settings
    with pytest.raises(CursorModelsError):
        load_models(settings)


def test_resolve_model_file_uses_projects_root_when_workspace_is_root(
    host_projects_settings,
) -> None:
    settings, workspace = host_projects_settings
    save_selected_model(settings, "gpt-5")
    model_file = resolve_model_file(settings, "/")
    assert model_file is not None
    assert model_file.parent == workspace
    assert model_file.read_text(encoding="utf-8").strip() == "gpt-5"


def test_parse_models_output_skips_headers() -> None:
    stdout = (
        "Available models\n"
        "\n"
        "auto - Auto (default)\n"
        "composer-2.5 - Composer 2.5 (current)\n"
        "Tip: use --model\n"
    )
    models = parse_models_output(stdout)
    assert models == [
        {"id": "auto", "label": "Auto (default)"},
        {"id": "composer-2.5", "label": "Composer 2.5 (current)"},
    ]


def test_refresh_roundtrip_writes_loadable_catalog(host_projects_settings) -> None:
    settings, _workspace = host_projects_settings
    models = parse_models_output("auto - Auto (default)\n")
    write_models_catalog(settings, models)
    assert load_models(settings) == models


def test_catalog_from_models_output_rejects_empty(host_projects_settings) -> None:
    settings, _workspace = host_projects_settings
    with pytest.raises(CursorModelsError):
        catalog_from_models_output(settings, "Available models\n\nTip: use --model\n")
