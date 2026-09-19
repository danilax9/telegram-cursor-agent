#!/bin/bash
set +e
export BOT_TOKEN=test-token
export ADMIN_TELEGRAM_ID=1
cd /root/telegram-cursor-agent

echo "=== Installing uv if missing ==="
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
else
  echo "uv already installed: $(command -v uv)"
fi

echo ""
echo "=== uv run pytest -q ==="
uv run pytest -q
PYTEST_EXIT=$?
echo "pytest exit code: $PYTEST_EXIT"

echo ""
echo "=== uv run ruff check . --fix ==="
uv run ruff check . --fix
RUFF_FIX_EXIT=$?
echo "ruff --fix exit code: $RUFF_FIX_EXIT"

echo ""
echo "=== uv run ruff check . ==="
uv run ruff check .
RUFF_EXIT=$?
echo "ruff check exit code: $RUFF_EXIT"

echo ""
echo "=== uv run mypy src ==="
uv run mypy src
MYPY_EXIT=$?
echo "mypy exit code: $MYPY_EXIT"

echo ""
echo "=== docker compose config -q ==="
docker compose config -q
DOCKER_EXIT=$?
echo "docker compose config exit code: $DOCKER_EXIT"

echo ""
echo "=== SUMMARY ==="
echo "pytest: $PYTEST_EXIT | ruff --fix: $RUFF_FIX_EXIT | ruff: $RUFF_EXIT | mypy: $MYPY_EXIT | docker: $DOCKER_EXIT"
