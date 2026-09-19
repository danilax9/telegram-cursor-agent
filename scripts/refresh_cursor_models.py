"""Refresh the Cursor CLI model catalog for the Telegram bot."""

import json
import subprocess
from pathlib import Path

result = subprocess.run(
    ["/root/.local/bin/agent", "models"], capture_output=True, text=True, check=True
)
models = []
for line in result.stdout.splitlines():
    if " - " not in line or line.startswith(("Available", "Tip:")):
        continue
    model_id, label = line.split(" - ", 1)
    if model_id and " " not in model_id:
        models.append({"id": model_id, "label": label})
Path("/root/telegram-cursor-agent/workspace/cursor-models.json").write_text(
    json.dumps(models, ensure_ascii=False)
)
print(f"models={len(models)}")
