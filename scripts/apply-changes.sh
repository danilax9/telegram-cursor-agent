#!/usr/bin/env bash
# Apply code changes without blocking the caller.
# Heavy work runs in background; this script exits in ~1 second.
set -euo pipefail

REPO_ROOT="${SELF_REPO_ROOT:-/root/telegram-cursor-agent}"
UV_BIN="${UV_BIN:-/root/.hermes/bin/uv}"
BOT_SERVICE="${BOT_COMPOSE_SERVICE:-bot}"
WORKER_SERVICE="${WORKER_SERVICE_NAME:-telegram-cursor-agent-worker}"
LOG_FILE="${APPLY_LOG_FILE:-/tmp/tca-apply.log}"
RESTART_KEY="${DEPLOY_WORKER_RESTART_KEY:-tca:worker:restart_pending}"
REDIS_PORT="${REDIS_PORT:-6380}"

log() {
  echo "[$(date -Iseconds)] [apply] $*" | tee -a "$LOG_FILE"
}

cd "$REPO_ROOT"
log "Queued background apply (pid will start shortly)..."

nohup bash -c "
set -euo pipefail
REPO_ROOT='${REPO_ROOT}'
UV_BIN='${UV_BIN}'
BOT_SERVICE='${BOT_SERVICE}'
WORKER_SERVICE='${WORKER_SERVICE}'
RESTART_KEY='${RESTART_KEY}'
REDIS_PORT='${REDIS_PORT}'
LOG_FILE='${LOG_FILE}'

log() {
  echo \"[\$(date -Iseconds)] [apply] \$*\" >>\"\$LOG_FILE\"
}

cd \"\$REPO_ROOT\"
log 'Installing package...'
\"\$UV_BIN\" pip install -e . >>\"\$LOG_FILE\" 2>&1

log 'Running migrations...'
\"\$UV_BIN\" run alembic upgrade head >>\"\$LOG_FILE\" 2>&1

if [[ -f deploy/telegram-cursor-agent-worker.service ]]; then
  log 'Updating worker unit...'
  cp deploy/telegram-cursor-agent-worker.service /etc/systemd/system/
  systemctl daemon-reload >>\"\$LOG_FILE\" 2>&1 || true
fi

if command -v docker >/dev/null 2>&1 && [[ -f docker-compose.yml ]]; then
  log 'Restarting bot container...'
  docker compose restart \"\$BOT_SERVICE\" >>\"\$LOG_FILE\" 2>&1
fi

log 'Requesting graceful worker restart...'
\"\$UV_BIN\" run python - <<'PY' >>\"\$LOG_FILE\" 2>&1
import redis

redis.Redis(host='127.0.0.1', port=int('${REDIS_PORT}'), db=0).set(
    '${RESTART_KEY}', '1', ex=600
)
PY

log 'Apply complete.'
" >>"$LOG_FILE" 2>&1 &

disown || true
echo "Apply started in background. Log: $LOG_FILE"
