#!/usr/bin/env bash
set +e
export BOT_TOKEN=test-token
export ADMIN_TELEGRAM_ID=1
cd /root/telegram-cursor-agent

OUT="/root/telegram-cursor-agent/.verify_output.txt"
: > "$OUT"

run_cmd() {
  local label="$1"
  shift
  {
    echo "=== $label ==="
    "$@" 2>&1
    echo "Exit code: $?"
    echo ""
  } | tee -a "$OUT"
}

echo "=== CHECK: /root/.local/bin/uv exists OR uv on PATH ===" | tee "$OUT"
if [ -x /root/.local/bin/uv ]; then
  echo "/root/.local/bin/uv exists and is executable" | tee -a "$OUT"
  USE_UV=1
elif command -v uv >/dev/null 2>&1; then
  echo "uv found on PATH: $(command -v uv)" | tee -a "$OUT"
  USE_UV=1
else
  echo "uv NOT found" | tee -a "$OUT"
  USE_UV=0
fi
echo "Exit code: 0" | tee -a "$OUT"
echo "" | tee -a "$OUT"

if [ "$USE_UV" -eq 1 ]; then
  run_cmd "uv run pytest -q" uv run pytest -q
  run_cmd "uv run ruff check . --fix" uv run ruff check . --fix
  run_cmd "uv run ruff check ." uv run ruff check .
  run_cmd "uv run mypy src" uv run mypy src
else
  run_cmd ".venv/bin/pytest -q" .venv/bin/pytest -q
  run_cmd ".venv/bin/ruff check . --fix" .venv/bin/ruff check . --fix
  run_cmd ".venv/bin/ruff check ." .venv/bin/ruff check .
  run_cmd ".venv/bin/mypy src" .venv/bin/mypy src
fi
run_cmd "docker compose config -q" docker compose config -q
