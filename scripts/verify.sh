#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

export BOT_TOKEN="${BOT_TOKEN:-test-token}"
export ADMIN_TELEGRAM_ID="${ADMIN_TELEGRAM_ID:-1}"

if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="${HOME}/.local/bin:${PATH}"
fi

uv run pytest -q
uv run ruff check . --fix
uv run ruff check .
uv run mypy src
docker compose config -q
