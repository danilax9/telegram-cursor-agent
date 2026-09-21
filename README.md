# Telegram Cursor Agent

Production-quality Telegram bot that remotely drives the Cursor CLI coding agent. Admins send natural-language prompts or shell commands; the bot queues work, enforces security boundaries, and streams results back.

## Stack

- Python 3.12 (compatible with 3.11+)
- aiogram 3
- async SQLAlchemy 2 + Alembic + PostgreSQL
- Redis task queue
- Pydantic Settings
- Docker Compose

## Quick Start

### One-command install (any server)

```bash
curl -fsSL https://raw.githubusercontent.com/danilax9/telegram-cursor-agent/main/install.sh | bash
```

Interactive installer asks for:

1. Telegram bot token (from @BotFather)
2. Your Telegram user ID (from @userinfobot)
3. Cursor login (opens a browser link)

The script installs Docker, uv, Cursor CLI, starts the bot stack, and registers the systemd worker.

From a cloned repo:

```bash
git clone https://github.com/danilax9/telegram-cursor-agent.git ~/telegram-cursor-agent
bash ~/telegram-cursor-agent/install.sh
```

Non-interactive (CI / automation):

```bash
BOT_TOKEN='123:abc' ADMIN_TELEGRAM_ID='123456789' TCA_SKIP_CURSOR_LOGIN=1 \
  TCA_NONINTERACTIVE=1 bash ~/telegram-cursor-agent/scripts/install.sh
```

### Uninstall

```bash
curl -fsSL https://raw.githubusercontent.com/danilax9/telegram-cursor-agent/main/uninstall.sh | bash
```

Or from the install directory:

```bash
bash ~/telegram-cursor-agent/scripts/uninstall.sh
```

By default uninstall stops services and removes the install directory, but keeps Docker volumes and Cursor auth unless you confirm otherwise.

### Manual setup

```bash
cp .env.example .env
# Edit .env: set TELEGRAM_BOT_TOKEN and TELEGRAM_ADMIN_IDS

docker compose up --build
```

Run migrations manually:

```bash
alembic upgrade head
```

Local development:

```bash
pip install -e ".[dev]"
alembic upgrade head
telegram-cursor-agent      # bot
telegram-cursor-worker     # background worker
```

## Usage

1. Add your Telegram user ID to `TELEGRAM_ADMIN_IDS`.
2. Send `/start` to the bot.
3. Send a coding prompt (routed to Cursor agent) or structured commands:
   - `help` — command list
   - `projects` — list discovered repos
   - `use project <name>` — select project
   - `git status` / `git diff` / `git log`
   - `run <command>` — shell command (sensitive commands require inline confirmation)
   - `cancel` — cancel running processes
   - `status` — pending confirmations/tasks

## Cursor CLI Integration

The adapter invokes the verified syntax:

```bash
cursor-agent --print --output-format stream-json --stream-partial-output \
  --workspace PATH --trust PROMPT
```

Resumption (only when a prior session exists):

```bash
cursor-agent ... --resume CHAT_ID PROMPT
```

### Cursor Constraints

- `cursor-agent` must be installed and on `PATH` (or set `CURSOR_AGENT_BIN`).
- `--trust` is required for non-interactive workspace access.
- `--workspace` must point to an allowed discovery root.
- `--resume` only works when `cursor_chat_id` was captured from a prior stream-json response.
- Output is streamed as JSON lines; partial output is enabled via `--stream-partial-output`.
- Long output is truncated and secrets redacted before Telegram delivery.

## Security

- **Admin-only**: non-admin Telegram users are rejected at middleware.
- **Path sandbox**: all filesystem access is canonicalized against `PROJECT_DISCOVERY_ROOTS`; traversal and forbidden segments (`.git`, `.env`, etc.) are blocked.
- **Command classifier**: safe / sensitive / forbidden tiers; destructive patterns blocked; sensitive ops require expiring inline confirmation (default 300s).
- **Git safeguards**: push, reset, merge, etc. blocked without confirmation.
- **Process isolation**: subprocesses run in new process groups; `cancel` sends SIGTERM/SIGKILL to the group.
- **Output hygiene**: API keys, tokens, and bearer credentials redacted; stdout truncated to `CURSOR_AGENT_MAX_OUTPUT_BYTES`.
- **Uploads**: stored under `UPLOAD_STORAGE_PATH` with size limits; path traversal on resolve blocked.

## Project Discovery

Scans `PROJECT_DISCOVERY_ROOTS` (max depth 4) for signature files:

`package.json`, `pyproject.toml`, `Cargo.toml`, `go.mod`, etc.

## Configuration

See `.env.example` for all settings.

## Testing

```bash
pytest -v
ruff check src tests
mypy src
```

## Architecture

```
telegram/          aiogram handlers, keyboards, middlewares
agent/             Cursor adapter, intent parser, sessions
projects/          discovery + persistence
git/               guarded git operations
execution/         runner, permissions, sandbox
database/          models + repositories
queue/             Redis queue + worker
services/          action routing, confirmations, uploads
core/              config, logging, security
```
