"""Refresh the Cursor CLI model catalog for the Telegram bot."""

from telegram_cursor_agent.core.config import get_settings
from telegram_cursor_agent.services.cursor_models import (
    models_catalog_path,
    refresh_models_catalog,
)

settings = get_settings()
models = refresh_models_catalog(settings)
print(f"models={len(models)} path={models_catalog_path(settings)}")
