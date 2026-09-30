"""Verified keyless OpenCode models for the Telegram model picker."""

from __future__ import annotations

DEFAULT_OPENCODE_MODEL_ID = "opencode/big-pickle"

OPENCODE_FREE_MODELS: list[dict[str, str]] = [
    {"id": "opencode/big-pickle", "label": "Big Pickle"},
    {"id": "opencode/longcat-2.5-preview-free", "label": "LongCat 2.5"},
    {"id": "opencode/mimo-v2.6-flash-free", "label": "MiMo V2.6 Flash"},
    {"id": "opencode/muse-spark-1.3-contributor-free", "label": "Muse Spark 1.3"},
    {"id": "opencode/nemotron-3-ultra-free", "label": "Nemotron 3 Ultra"},
    {"id": "opencode/nemotron-3.5-lightning-free", "label": "Nemotron 3.5 Lightning"},
    {"id": "opencode/space-bunny-free", "label": "Space Bunny"},
]


def is_opencode_model(model_id: str | None) -> bool:
    return bool(model_id and model_id.startswith("opencode/"))


def merge_opencode_models(models: list[dict[str, str]]) -> list[dict[str, str]]:
    seen = {str(item.get("id")) for item in models}
    extra = [item for item in OPENCODE_FREE_MODELS if item["id"] not in seen]
    return [*models, *extra]
