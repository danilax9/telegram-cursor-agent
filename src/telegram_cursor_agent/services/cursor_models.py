"""Cursor model catalog and per-user model selection."""

from __future__ import annotations

import json
from pathlib import Path

from telegram_cursor_agent.core.config import Settings

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
    path = models_catalog_path(settings)
    if not path.is_file():
        raise CursorModelsError(
            "Каталог моделей не найден. "
            "На сервере выполни: `uv run python scripts/refresh_cursor_models.py`"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CursorModelsError("Не удалось прочитать каталог моделей.") from exc
    if not isinstance(raw, list) or not raw:
        raise CursorModelsError("Каталог моделей пуст.")
    return raw


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


def write_models_catalog(settings: Settings, models: list[dict[str, str]]) -> Path:
    path = models_catalog_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(models, ensure_ascii=False), encoding="utf-8")
    return path
