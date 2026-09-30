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
    # Isolated Cursor auth: these tests assert on logged-out behaviour and
    # must not see the real auth.json present on the host.
    monkeypatch.setenv("CURSOR_AUTH_FILE", str(tmp_path / "cursor-home/auth.json"))
    monkeypatch.setenv(
        "CURSOR_ACCOUNTS_FILE", str(tmp_path / "cursor-accounts.json")
    )
    monkeypatch.setenv("CURSOR_ACCOUNTS_DIR", str(tmp_path / "cursor-accounts"))
    clear_settings_cache()
    from telegram_cursor_agent.core.config import get_settings

    return get_settings(), workspace


@pytest.fixture
def logged_in_settings(host_projects_settings):
    """Settings with a Cursor auth file, so the catalog half is visible."""
    settings, _workspace = host_projects_settings
    import json as _json
    from pathlib import Path as _Path

    root = _Path(settings.cursor_accounts_dir).parent
    root.mkdir(parents=True, exist_ok=True)
    auth = root / "auth-for-catalog.json"
    auth.write_text('{"accessToken":"t"}', encoding="utf-8")
    settings.cursor_accounts_file = root / "accounts-for-catalog.json"
    settings.cursor_accounts_file.write_text(
        _json.dumps(
            {
                "auto_rotate": True,
                "usage_threshold_percent": 95.0,
                "accounts": [
                    {
                        "id": "main",
                        "label": "Main",
                        "auth_file": str(auth),
                        "priority": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    settings.cursor_auth_file = auth
    return settings


def test_effective_projects_root_uses_existing_path(host_projects_settings) -> None:
    settings, workspace = host_projects_settings
    assert effective_projects_root(settings) == workspace
    assert settings.effective_projects_root == workspace


def test_load_models_reads_catalog(logged_in_settings) -> None:
    settings = logged_in_settings
    workspace = effective_projects_root(settings)
    (workspace / "cursor-models.json").write_text(
        json.dumps([{"id": "auto", "label": "Auto"}]),
        encoding="utf-8",
    )
    models = load_models(settings)
    assert models[0]["id"] == "auto"


def test_load_models_hides_cursor_catalog_when_logged_out(
    host_projects_settings,
) -> None:
    settings, workspace = host_projects_settings
    (workspace / "cursor-models.json").write_text(
        json.dumps([{"id": "auto", "label": "Auto"}]),
        encoding="utf-8",
    )
    models = load_models(settings)
    assert [item["id"] for item in models] == [
        "opencode/big-pickle",
        "opencode/longcat-2.5-preview-free",
        "opencode/mimo-v2.6-flash-free",
        "opencode/muse-spark-1.3-contributor-free",
        "opencode/nemotron-3-ultra-free",
        "opencode/nemotron-3.5-lightning-free",
        "opencode/space-bunny-free",
    ]


def test_load_models_missing_file_still_lists_opencode(host_projects_settings) -> None:
    settings, _workspace = host_projects_settings
    models = load_models(settings)
    assert any(item["id"] == "opencode/big-pickle" for item in models)


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


def test_refresh_roundtrip_writes_loadable_catalog(logged_in_settings) -> None:
    settings = logged_in_settings
    models = parse_models_output("auto - Auto (default)\n")
    write_models_catalog(settings, models)
    loaded = load_models(settings)
    assert loaded[: len(models)] == models
    assert any(item["id"].startswith("opencode/") for item in loaded)


def test_catalog_from_models_output_rejects_empty(host_projects_settings) -> None:
    settings, _workspace = host_projects_settings
    with pytest.raises(CursorModelsError):
        catalog_from_models_output(settings, "Available models\n\nTip: use --model\n")
