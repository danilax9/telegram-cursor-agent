"""Cursor model catalog and per-user model selection."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.services.opencode_models import (
    DEFAULT_OPENCODE_MODEL_ID,
    merge_opencode_models,
)

MODELS_FILENAME = "cursor-models.json"
MODEL_SELECTION_FILENAME = ".cursor_model"


class CursorModelsError(Exception):
    """Raised when the model catalog cannot be loaded."""


def effective_projects_root(settings: Settings) -> Path:
    """Resolve the workspace root that exists in the current runtime."""
    root = settings.projects_root
    if root.is_dir():
        return root
    container_root = Path("/workspace")
    if container_root.is_dir():
        return container_root
    return root


def models_catalog_path(settings: Settings) -> Path:
    return effective_projects_root(settings) / MODELS_FILENAME


def models_catalog_is_present(settings: Settings) -> bool:
    return models_catalog_path(settings).is_file()


def model_selection_path(settings: Settings) -> Path:
    return effective_projects_root(settings) / MODEL_SELECTION_FILENAME


def resolve_model_file(settings: Settings, workspace: str) -> Path | None:
    """Find the selected model file for a Cursor agent workspace."""
    candidates = [
        Path(workspace) / MODEL_SELECTION_FILENAME,
        effective_projects_root(settings) / MODEL_SELECTION_FILENAME,
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def load_models(settings: Settings) -> list[dict[str, str]]:
    """Cursor catalog plus keyless OpenCode models.

    A missing or empty Cursor catalog is fine: OpenCode still works before login.
    """
    path = models_catalog_path(settings)
    raw: list[dict[str, str]] = []
    if path.is_file():
        try:
            parsed = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CursorModelsError("Не удалось прочитать каталог моделей.") from exc
        if isinstance(parsed, list):
            raw = [
                item
                for item in parsed
                if isinstance(item, dict) and item.get("id")
            ]
    models = merge_opencode_models(raw)
    if not models:
        raise CursorModelsError("Каталог моделей пуст.")
    return models


def load_selected_model_id(settings: Settings) -> str | None:
    path = model_selection_path(settings)
    if not path.is_file():
        return DEFAULT_OPENCODE_MODEL_ID
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return DEFAULT_OPENCODE_MODEL_ID
    return raw or DEFAULT_OPENCODE_MODEL_ID


def resolve_model_label(models: list[dict[str, str]], model_id: str) -> str:
    for item in models:
        if item.get("id") == model_id:
            return str(item["label"])
    return model_id


def save_selected_model(settings: Settings, model_id: str) -> None:
    path = model_selection_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{model_id}\n", encoding="utf-8")


def parse_models_output(stdout: str) -> list[dict[str, str]]:
    models: list[dict[str, str]] = []
    for line in stdout.splitlines():
        if " - " not in line or line.startswith(("Available", "Tip:")):
            continue
        model_id, label = line.split(" - ", 1)
        model_id = model_id.strip()
        if model_id and " " not in model_id:
            models.append({"id": model_id, "label": label.strip()})
    return models


REFRESH_MODELS_MENU_UPDATED = "__refresh_models_menu_updated__"


def write_models_catalog(settings: Settings, models: list[dict[str, str]]) -> Path:
    path = models_catalog_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(models, ensure_ascii=False), encoding="utf-8")
    return path


def catalog_from_models_output(
    settings: Settings, stdout: str
) -> list[dict[str, str]]:
    """Parse `cursor-agent models` output and persist the catalog."""
    models = parse_models_output(stdout)
    if not models:
        raise CursorModelsError("cursor-agent models returned no entries")
    write_models_catalog(settings, models)
    return models


def refresh_models_catalog(settings: Settings) -> list[dict[str, str]]:
    """Fetch models from Cursor CLI and write `cursor-models.json`."""
    try:
        result = subprocess.run(
            [settings.cursor_agent_bin, "models"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        raise CursorModelsError(
            "Не удалось обновить каталог моделей через cursor-agent."
        ) from exc
    return catalog_from_models_output(settings, result.stdout)
