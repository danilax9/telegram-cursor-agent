#!/usr/bin/env bash
# Install package and request a graceful worker restart (no hard kill).
set -euo pipefail

REPO_ROOT="${SELF_REPO_ROOT:-/root/telegram-cursor-agent}"
UV_BIN="${UV_BIN:-/root/.hermes/bin/uv}"
RESTART_KEY="${DEPLOY_WORKER_RESTART_KEY:-tca:worker:restart_pending}"

cd "$REPO_ROOT"
"$UV_BIN" pip install -e .
if [[ -f "${REPO_ROOT}/scripts/deploy-notify-start.py" ]]; then
  "$UV_BIN" run python "${REPO_ROOT}/scripts/deploy-notify-start.py" || true
fi
"$UV_BIN" run python - <<PY
import redis
redis.Redis(host="127.0.0.1", port=6380, db=0).set("${RESTART_KEY}", "1", ex=600)
PY
echo "Installed. Worker will restart after the current task."
