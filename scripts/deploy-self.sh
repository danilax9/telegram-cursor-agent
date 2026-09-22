#!/usr/bin/env bash
# Self-deploy: reinstall, migrate, restart bot container and worker.
set -euo pipefail

REPO_ROOT="${SELF_REPO_ROOT:-/root/telegram-cursor-agent}"
WORKER_SERVICE="${WORKER_SERVICE_NAME:-telegram-cursor-agent-worker}"
BOT_SERVICE="${BOT_COMPOSE_SERVICE:-bot}"
LOG_FILE="${DEPLOY_LOG_FILE:-/tmp/tca-deploy.log}"

resolve_uv_bin() {
  if [[ -n "${UV_BIN:-}" && -x "${UV_BIN}" ]]; then
    return 0
  fi
  if command -v uv >/dev/null 2>&1; then
    UV_BIN="$(command -v uv)"
    return 0
  fi
  for candidate in \
    /root/.hermes/bin/uv \
    "${HOME}/.local/bin/uv" \
    /usr/local/bin/uv; do
    if [[ -x "$candidate" ]]; then
      UV_BIN="$candidate"
      return 0
    fi
  done
  echo "[deploy] uv not found (set UV_BIN to the uv binary path)" >&2
  return 1
}

resolve_uv_bin
export UV_BIN

log() {
  echo "[$(date -Iseconds)] [deploy] $*" | tee -a "$LOG_FILE"
}

cd "$REPO_ROOT"

log "Notifying user about deploy restart..."
if [[ -f "${REPO_ROOT}/scripts/deploy-notify-start.py" ]]; then
  "$UV_BIN" run python "${REPO_ROOT}/scripts/deploy-notify-start.py" >>"$LOG_FILE" 2>&1 || true
fi

log "Installing package..."
"$UV_BIN" pip install -e .

log "Running migrations..."
"$UV_BIN" run alembic upgrade head

if command -v docker >/dev/null 2>&1 && [[ -f docker-compose.yml ]]; then
  # src/ is bind-mounted — Python loads code at process start, so recreate the bot.
  compose_args=(up -d --force-recreate --no-deps)
  if [[ "${DEPLOY_FORCE_BUILD:-}" == "1" ]]; then
    log "Building and recreating bot container ($BOT_SERVICE)..."
    compose_args=(up -d --build --force-recreate --no-deps)
  else
    log "Recreating bot container without image rebuild ($BOT_SERVICE)..."
  fi
  DATABASE_URL=postgresql+asyncpg://tca:tca_secret@postgres:5432/telegram_cursor_agent \
  REDIS_URL=redis://redis:6379/0 \
    docker compose "${compose_args[@]}" "$BOT_SERVICE"
else
  log "Docker compose not available; skipping bot container restart."
fi

REDIS_CLI="${REDIS_CLI:-redis-cli}"
REDIS_PORT="${REDIS_PORT:-6380}"
WORKER_READY_KEY="${DEPLOY_WORKER_READY_KEY:-tca:worker:ready}"

if command -v "$REDIS_CLI" >/dev/null 2>&1; then
  log "Clearing stale worker ready key..."
  "$REDIS_CLI" -p "$REDIS_PORT" DEL "$WORKER_READY_KEY" >/dev/null 2>&1 || true
fi

WORKER_RESTART_KEY="${DEPLOY_WORKER_RESTART_KEY:-tca:worker:restart_pending}"
if command -v "$REDIS_CLI" >/dev/null 2>&1; then
  log "Scheduling graceful worker restart after current task..."
  "$REDIS_CLI" -p "$REDIS_PORT" SET "$WORKER_RESTART_KEY" 1 EX 600 >/dev/null 2>&1 || true
else
  log "Redis unavailable; scheduling delayed worker restart ($WORKER_SERVICE)..."
  nohup bash -c "sleep 60 && systemctl restart ${WORKER_SERVICE}" >>"$LOG_FILE" 2>&1 &
  disown || true
fi

MARKER="${REPO_ROOT}/.deploy-pending.json"
if [[ -f "$MARKER" ]]; then
  MARKER_PATH="$MARKER" "$UV_BIN" run python - <<'PY'
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
import os

path = Path(os.environ["MARKER_PATH"])
data = json.loads(path.read_text())
data["status"] = "pending_resume"
data["completed_at"] = datetime.now(UTC).isoformat()
data["recovery_token"] = str(uuid.uuid4())
data.pop("deploy_log", None)
path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
PY
else
  DEPLOY_TELEGRAM_ID="${DEPLOY_TELEGRAM_ID:-}"
  DEPLOY_SESSION_ID="${DEPLOY_SESSION_ID:-}"
  DEPLOY_CURSOR_CHAT_ID="${DEPLOY_CURSOR_CHAT_ID:-}"
  DEPLOY_WORKSPACE="${DEPLOY_WORKSPACE:-$REPO_ROOT}"
  DEPLOY_USER_ID="${DEPLOY_USER_ID:-}"
  DEPLOY_TASK_ID="${DEPLOY_TASK_ID:-}"
  MARKER_PATH="$MARKER" \
  DEPLOY_TELEGRAM_ID="$DEPLOY_TELEGRAM_ID" \
  DEPLOY_SESSION_ID="$DEPLOY_SESSION_ID" \
  DEPLOY_CURSOR_CHAT_ID="$DEPLOY_CURSOR_CHAT_ID" \
  DEPLOY_WORKSPACE="$DEPLOY_WORKSPACE" \
  DEPLOY_USER_ID="$DEPLOY_USER_ID" \
  DEPLOY_TASK_ID="$DEPLOY_TASK_ID" \
  "$UV_BIN" run python - <<'PY'
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from telegram_cursor_agent.core.config import get_settings
from telegram_cursor_agent.database.session import create_engine, create_session_factory
from telegram_cursor_agent.services.deploy_context_snapshot import (
    ensure_marker,
    snapshot_running_deploy_context,
)
from telegram_cursor_agent.services.deploy_resume import DeployContext

marker_path = Path(os.environ["MARKER_PATH"])
settings = get_settings()


async def build_context() -> DeployContext:
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        context = await snapshot_running_deploy_context(session_factory)
    finally:
        await engine.dispose()
    if context is not None:
        return context

    def _uuid(value: str) -> str | None:
        return value or None

    telegram_raw = os.environ.get("DEPLOY_TELEGRAM_ID", "")
    return DeployContext(
        task_id=uuid.UUID(os.environ["DEPLOY_TASK_ID"]) if os.environ.get("DEPLOY_TASK_ID") else None,
        user_id=uuid.UUID(os.environ["DEPLOY_USER_ID"]) if os.environ.get("DEPLOY_USER_ID") else None,
        telegram_id=int(telegram_raw) if telegram_raw else None,
        session_id=uuid.UUID(os.environ["DEPLOY_SESSION_ID"]) if os.environ.get("DEPLOY_SESSION_ID") else None,
        cursor_chat_id=_uuid(os.environ.get("DEPLOY_CURSOR_CHAT_ID", "")),
        workspace=os.environ.get("DEPLOY_WORKSPACE") or str(settings.self_repo_root),
    )


context = asyncio.run(build_context())
ensure_marker(settings, context)
payload = {
    "version": 1,
    "status": "pending_resume",
    "task_id": str(context.task_id) if context.task_id else None,
    "user_id": str(context.user_id) if context.user_id else None,
    "telegram_id": context.telegram_id,
    "session_id": str(context.session_id) if context.session_id else None,
    "cursor_chat_id": context.cursor_chat_id,
    "workspace": context.workspace,
    "recovery_token": str(uuid.uuid4()),
    "created_at": datetime.now(UTC).isoformat(),
    "completed_at": datetime.now(UTC).isoformat(),
}
marker_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
PY
fi

log "Deploy complete. Worker restarts after the current task finishes."
