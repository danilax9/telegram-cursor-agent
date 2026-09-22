"""Selected model file helpers."""

from pathlib import Path

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.services.cursor_models import (
    load_selected_model_id,
    resolve_model_label,
    save_selected_model,
)


def test_load_and_resolve_selected_model(tmp_path: Path, test_settings: Settings) -> None:
    settings = test_settings.model_copy(update={"projects_root": tmp_path})
    save_selected_model(settings, "gpt-5.2")
    assert load_selected_model_id(settings) == "gpt-5.2"
    label = resolve_model_label(
        [{"id": "gpt-5.2", "label": "GPT-5.2"}],
        "gpt-5.2",
    )
    assert label == "GPT-5.2"
