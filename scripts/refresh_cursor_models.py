"""Refresh the Cursor CLI model catalog for the Telegram bot."""

import subprocess

from telegram_cursor_agent.core.config import get_settings
from telegram_cursor_agent.services.cursor_models import (
    parse_models_output,
    write_models_catalog,
)

settings = get_settings()
result = subprocess.run(
    [settings.cursor_agent_bin, "models"],
    capture_output=True,
    text=True,
    check=True,
)
models = parse_models_output(result.stdout)
path = write_models_catalog(settings, models)
print(f"models={len(models)} path={path}")
