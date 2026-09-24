#!/usr/bin/env bash
# Interactive installer for Telegram Cursor Agent.
set -euo pipefail

TCA_VERSION="0.1.0"
TCA_INSTALL_DIR="${TCA_INSTALL_DIR:-}"
TCA_REPO_URL="${TCA_REPO_URL:-}"
TCA_REPO_BRANCH="${TCA_REPO_BRANCH:-main}"
TCA_SKIP_CURSOR_LOGIN="${TCA_SKIP_CURSOR_LOGIN:-0}"
TCA_NONINTERACTIVE="${TCA_NONINTERACTIVE:-0}"
TCA_DRY_RUN="${TCA_DRY_RUN:-0}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

CURSOR_AUTH_FILE="${CURSOR_AUTH_FILE:-${HOME}/.config/cursor/auth.json}"
CURSOR_ACCOUNTS_DIR="${CURSOR_ACCOUNTS_DIR:-${HOME}/.cursor-accounts}"
CURSOR_ACCOUNTS_FILE="${CURSOR_ACCOUNTS_FILE:-${CURSOR_ACCOUNTS_DIR}/accounts.json}"
CURSOR_CLI="${CURSOR_CLI_PATH:-agent}"
UV_BIN="${UV_BIN:-${HOME}/.local/bin/uv}"
WORKER_SERVICE="${WORKER_SERVICE_NAME:-telegram-cursor-agent-worker}"
LOGIN_TIMEOUT="${CURSOR_ACCOUNT_LOGIN_TIMEOUT_SECONDS:-600}"

log() {
  echo "[install] $*" >&2
}

warn() {
  echo "[install] WARNING: $*" >&2
}

die() {
  echo "[install] ERROR: $*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

is_interactive() {
  if [[ "${TCA_NONINTERACTIVE}" == "1" ]]; then
    return 1
  fi
  [[ -t 0 || -r /dev/tty ]]
}

read_prompt() {
  if [[ -t 0 ]]; then
    read -r "$@"
  else
    read -r "$@" </dev/tty
  fi
}

run() {
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    log "dry-run: $*"
    return 0
  fi
  "$@"
}

prompt() {
  local message="$1"
  local default="${2:-}"
  local value=""
  if ! is_interactive; then
    if [[ -n "${default}" ]]; then
      echo "${default}"
      return 0
    fi
    die "Missing required value for: ${message}"
  fi
  if [[ -t 0 ]]; then
    if [[ -n "${default}" ]]; then
      read -r -p "${message} [${default}]: " value
    else
      read -r -p "${message}: " value
    fi
  else
    if [[ -n "${default}" ]]; then
      echo -n "${message} [${default}]: " >/dev/tty
    else
      echo -n "${message}: " >/dev/tty
    fi
    read -r value </dev/tty
    echo "" >/dev/tty
  fi
  if [[ -n "${default}" ]]; then
    echo "${value:-${default}}"
  else
    echo "${value}"
  fi
}

prompt_secret() {
  local message="$1"
  local value=""
  if ! is_interactive; then
    if [[ -n "${BOT_TOKEN:-}" ]]; then
      echo "${BOT_TOKEN}"
      return 0
    fi
    die "Missing BOT_TOKEN in non-interactive mode"
  fi
  # Do not use read -s: silent mode breaks paste in many SSH terminals.
  if [[ -t 0 ]]; then
    echo "Вставка: Ctrl+Shift+V или ПКМ. Ввод виден на экране."
    read -r -p "${message}: " value
  else
    echo "Вставка: Ctrl+Shift+V или ПКМ. Ввод виден на экране." >/dev/tty
    echo -n "${message}: " >/dev/tty
    read -r value </dev/tty
    echo "" >/dev/tty
  fi
  # Trim accidental whitespace from paste.
  value="${value#"${value%%[![:space:]]*}"}"
  value="${value%"${value##*[![:space:]]}"}"
  if [[ -z "${value}" ]]; then
    die "Значение не может быть пустым"
  fi
  echo "${value}"
}

validate_telegram_id() {
  local raw="$1"
  if [[ ! "${raw}" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    die "Telegram ID должен быть числом или списком через запятую, например: 123456789"
  fi
}

validate_bot_token() {
  local token="$1"
  if [[ ! "${token}" =~ ^[0-9]+:[A-Za-z0-9_-]+$ ]]; then
    die "Неверный формат токена Telegram-бота"
  fi
}

ensure_path() {
  export PATH="${HOME}/.local/bin:${PATH}"
}

detect_repo_root() {
  if [[ -f "${REPO_ROOT}/pyproject.toml" && -f "${REPO_ROOT}/docker-compose.yml" ]]; then
    echo "${REPO_ROOT}"
    return 0
  fi
  if [[ -n "${TCA_INSTALL_DIR}" && -f "${TCA_INSTALL_DIR}/pyproject.toml" ]]; then
    echo "${TCA_INSTALL_DIR}"
    return 0
  fi
  echo ""
}

install_system_packages() {
  if need_cmd apt-get; then
    log "Installing system packages (git, curl, ca-certificates)..."
    run apt-get update -qq
    run apt-get install -y -qq git curl ca-certificates gnupg lsb-release xz-utils
  elif need_cmd dnf; then
    log "Installing system packages via dnf..."
    run dnf install -y git curl ca-certificates
  elif need_cmd yum; then
    log "Installing system packages via yum..."
    run yum install -y git curl ca-certificates
  else
    warn "Unknown package manager; ensure git and curl are installed"
  fi
}

install_node_for_mcp() {
  if need_cmd npx && npx --version >/dev/null 2>&1; then
    log "Node.js / npx already available: $(command -v npx)"
    return 0
  fi
  if [[ -x /usr/local/bin/npx ]] && /usr/local/bin/npx --version >/dev/null 2>&1; then
    log "Node.js / npx already installed in /usr/local/bin"
    return 0
  fi
  local node_ver="${TCA_NODE_VERSION:-22.14.0}"
  local arch="linux-x64"
  local uname_m
  uname_m="$(uname -m)"
  if [[ "${uname_m}" == "aarch64" || "${uname_m}" == "arm64" ]]; then
    arch="linux-arm64"
  fi
  local tarball="node-v${node_ver}-${arch}.tar.xz"
  local url="https://nodejs.org/dist/v${node_ver}/${tarball}"
  log "Installing Node.js ${node_ver} for MCP (npx)..."
  run curl -fsSL "${url}" -o "/tmp/${tarball}"
  run tar -xJf "/tmp/${tarball}" -C /usr/local --strip-components=1
  run rm -f "/tmp/${tarball}"
  if ! /usr/local/bin/npx --version >/dev/null 2>&1; then
    warn "npx не установился — MCP через npm могут не работать"
    return 0
  fi
  log "npx: /usr/local/bin/npx ($( /usr/local/bin/node -v ))"
}

install_docker() {
  if ! need_cmd docker || ! docker compose version >/dev/null 2>&1; then
    log "Installing Docker..."
    if [[ "${TCA_DRY_RUN}" == "1" ]]; then
      log "dry-run: curl -fsSL https://get.docker.com | sh"
      return 0
    fi
    curl -fsSL https://get.docker.com | sh
  else
    log "Docker already installed"
  fi
  ensure_docker_running
}

ensure_docker_running() {
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    return 0
  fi
  if ! need_cmd docker; then
    die "Docker CLI not found after installation"
  fi
  if need_cmd systemctl; then
    run systemctl enable docker >/dev/null 2>&1 || true
    if ! systemctl is-active docker >/dev/null 2>&1; then
      log "Starting Docker daemon..."
      run systemctl start docker
    fi
  fi
  if ! docker info >/dev/null 2>&1; then
    die "Docker daemon is not running. Start it: systemctl start docker"
  fi
}

ensure_disk_space() {
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    return 0
  fi
  local avail_kb
  avail_kb="$(df -Pk / | awk "NR==2 {print \$4}")"
  if [[ "${avail_kb}" -lt 1048576 ]]; then
    warn "Low disk space on / (<1 GB free). Attempting cleanup..."
    if need_cmd journalctl; then
      run journalctl --vacuum-size=80M >/dev/null 2>&1 || true
    fi
    if need_cmd apt-get; then
      run apt-get clean >/dev/null 2>&1 || true
      run apt-get autoremove -y >/dev/null 2>&1 || true
    fi
    if need_cmd docker; then
      run docker system prune -af >/dev/null 2>&1 || true
    fi
    avail_kb="$(df -Pk / | awk "NR==2 {print \$4}")"
    if [[ "${avail_kb}" -lt 524288 ]]; then
      die "Not enough disk space on / (<512 MB free). Free space and retry."
    fi
  fi
}

install_uv() {
  if [[ -x "${UV_BIN}" ]] || need_cmd uv; then
    UV_BIN="$(command -v uv || echo "${UV_BIN}")"
    log "uv already installed: ${UV_BIN}"
    return 0
  fi
  log "Installing uv..."
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    log "dry-run: curl -LsSf https://astral.sh/uv/install.sh | sh"
    return 0
  fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
  UV_BIN="${HOME}/.local/bin/uv"
}

install_cursor_cli() {
  ensure_path
  if need_cmd "${CURSOR_CLI}" || [[ -x "${HOME}/.local/bin/${CURSOR_CLI}" ]]; then
    CURSOR_CLI="$(command -v "${CURSOR_CLI}" || echo "${HOME}/.local/bin/${CURSOR_CLI}")"
    log "Cursor CLI already installed: ${CURSOR_CLI}"
    return 0
  fi
  if [[ -x "${HOME}/.local/bin/agent" ]]; then
    CURSOR_CLI="${HOME}/.local/bin/agent"
    log "Cursor CLI found: ${CURSOR_CLI}"
    return 0
  fi
  log "Installing Cursor CLI..."
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    log "dry-run: curl -fsSL https://cursor.com/install | bash"
    return 0
  fi
  curl -fsSL https://cursor.com/install | bash
  ensure_path
  CURSOR_CLI="$(command -v agent || command -v cursor-agent || true)"
  if [[ -z "${CURSOR_CLI}" ]]; then
    die "Cursor CLI not found after installation"
  fi
}

assert_install_root() {
  local install_root="$1"
  if [[ ! -f "${install_root}/pyproject.toml" ]]; then
    die "Invalid install directory (pyproject.toml missing): ${install_root}"
  fi
}

clone_or_update_repo() {
  local target="$1"
  if [[ -f "${target}/pyproject.toml" ]]; then
    log "Using existing repo at ${target}"
    if [[ -d "${target}/.git" ]] && [[ "${TCA_DRY_RUN}" != "1" ]]; then
      log "Updating repository..."
      git -C "${target}" pull --ff-only >&2 || warn "git pull failed; continuing with local copy"
    fi
    assert_install_root "${target}"
    return 0
  fi

  if [[ -z "${TCA_REPO_URL}" ]]; then
    die "Repository not found at ${target}. Set TCA_REPO_URL to clone it."
  fi

  log "Cloning ${TCA_REPO_URL} -> ${target}"
  run mkdir -p "$(dirname "${target}")"
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    log "dry-run: git clone --branch ${TCA_REPO_BRANCH} ${TCA_REPO_URL} ${target}"
    return 0
  fi
  git clone --branch "${TCA_REPO_BRANCH}" --depth 1 "${TCA_REPO_URL}" "${target}" >&2
  assert_install_root "${target}"
}

extract_login_url() {
  local log_file="$1"
  grep -Eo 'https://cursor\.com/loginDeepControl\?[^[:space:]]+' "${log_file}" | head -1
}

cursor_login_interactive() {
  if [[ "${TCA_SKIP_CURSOR_LOGIN}" == "1" ]]; then
    log "Skipping Cursor login (TCA_SKIP_CURSOR_LOGIN=1)"
    return 0
  fi
  if [[ -f "${CURSOR_AUTH_FILE}" ]]; then
    if is_interactive; then
      local reuse=""
      if [[ -t 0 ]]; then
        read -r -p "Cursor уже авторизован (${CURSOR_AUTH_FILE}). Перелогиниться? [y/N]: " reuse
      else
        echo -n "Cursor уже авторизован (${CURSOR_AUTH_FILE}). Перелогиниться? [y/N]: " >/dev/tty
        read -r reuse </dev/tty
        echo "" >/dev/tty
      fi
      if [[ ! "${reuse}" =~ ^[Yy]$ ]]; then
        log "Using existing Cursor auth"
        return 0
      fi
    else
      log "Using existing Cursor auth at ${CURSOR_AUTH_FILE}"
      return 0
    fi
  fi

  ensure_path
  mkdir -p "$(dirname "${CURSOR_AUTH_FILE}")"
  local login_log
  login_log="$(mktemp)"
  trap 'rm -f "${login_log}"' RETURN

  log "Starting Cursor login..."
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    log "dry-run: ${CURSOR_CLI} login"
    return 0
  fi

  export NO_OPEN_BROWSER=1
  "${CURSOR_CLI}" login >"${login_log}" 2>&1 &
  local login_pid=$!

  local login_url=""
  local waited=0
  while [[ -z "${login_url}" && ${waited} -lt 60 ]]; do
    login_url="$(extract_login_url "${login_log}" || true)"
    if [[ -n "${login_url}" ]]; then
      break
    fi
    if ! kill -0 "${login_pid}" 2>/dev/null; then
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done

  if [[ -z "${login_url}" ]]; then
    kill "${login_pid}" 2>/dev/null || true
    cat "${login_log}" >&2 || true
    die "Не удалось получить ссылку для входа в Cursor"
  fi

  echo ""
  echo "=============================================="
  echo "  Вход в аккаунт Cursor"
  echo "=============================================="
  echo ""
  echo "Откройте ссылку в браузере и войдите в Cursor:"
  echo ""
  echo "  ${login_url}"
  echo ""
  echo "Ожидание завершения входа (до ${LOGIN_TIMEOUT} сек)..."
  echo ""

  local elapsed=0
  while [[ ${elapsed} -lt ${LOGIN_TIMEOUT} ]]; do
    if [[ -f "${CURSOR_AUTH_FILE}" ]]; then
      wait "${login_pid}" 2>/dev/null || true
      log "Cursor login successful"
      return 0
    fi
    if ! kill -0 "${login_pid}" 2>/dev/null; then
      break
    fi
    sleep 2
    elapsed=$((elapsed + 2))
  done

  kill "${login_pid}" 2>/dev/null || true
  die "Время ожидания входа в Cursor истекло"
}

write_accounts_json() {
  run mkdir -p "${CURSOR_ACCOUNTS_DIR}"
  if [[ -f "${CURSOR_ACCOUNTS_FILE}" ]]; then
    log "Keeping existing ${CURSOR_ACCOUNTS_FILE}"
    return 0
  fi
  log "Creating ${CURSOR_ACCOUNTS_FILE}"
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    return 0
  fi
  cat >"${CURSOR_ACCOUNTS_FILE}" <<EOF
{
  "auto_rotate": true,
  "usage_threshold_percent": 95.0,
  "accounts": [
    {
      "id": "main",
      "label": "Основной",
      "auth_file": "${CURSOR_AUTH_FILE}",
      "priority": 0
    }
  ]
}
EOF
}

write_env_file() {
  local install_root="$1"
  local bot_token="$2"
  local admin_ids="$3"
  local env_file="${install_root}/.env"

  log "Writing ${env_file}"
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    return 0
  fi

  cat >"${env_file}" <<EOF
# Generated by scripts/install.sh on $(date -Iseconds)
BOT_TOKEN=${bot_token}
ADMIN_TELEGRAM_ID=${admin_ids}

DATABASE_URL=postgresql+asyncpg://tca:tca_secret@127.0.0.1:5433/telegram_cursor_agent
REDIS_URL=redis://127.0.0.1:6380/0

CURSOR_CLI_PATH=${CURSOR_CLI}
CURSOR_AUTH_FILE=${CURSOR_AUTH_FILE}
CURSOR_ACCOUNTS_FILE=${CURSOR_ACCOUNTS_FILE}
CURSOR_ACCOUNTS_DIR=${CURSOR_ACCOUNTS_DIR}
CURSOR_ACCOUNT_LOGIN_TIMEOUT_SECONDS=${LOGIN_TIMEOUT}
TASK_TIMEOUT=3600

PROJECTS_ROOT=${install_root}/workspace
PROJECT_SEARCH_ROOTS=${install_root}/workspace,${install_root}
ALLOWED_PROJECT_ROOTS=/
SANDBOX_OPEN=true
SELF_DEPLOY_ENABLED=true
SELF_REPO_ROOT=${install_root}
AGENT_WORKSPACE=/
DEPLOY_SCRIPT=${install_root}/scripts/deploy-self.sh
WORKER_SERVICE_NAME=${WORKER_SERVICE}
BOT_COMPOSE_SERVICE=bot

UPLOAD_STORAGE_PATH=${install_root}/data/uploads
UPLOAD_HOST_PATH=${install_root}/data/uploads
MAX_UPLOAD_BYTES=10485760

LOG_LEVEL=INFO
APP_ENV=production

UV_BIN=${UV_BIN}
CURSOR_MCP_CONFIG_PATH=${HOME}/.cursor/mcp.json
CURSOR_APPROVE_MCPS=true
EOF
  chmod 600 "${env_file}"
}

install_python_package() {
  local install_root="$1"
  log "Installing Python package..."
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    return 0
  fi
  cd "${install_root}"
  "${UV_BIN}" sync --all-extras
}

run_migrations() {
  local install_root="$1"
  log "Waiting for PostgreSQL..."
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    return 0
  fi
  local i=0
  while [[ ${i} -lt 60 ]]; do
    if docker compose -f "${install_root}/docker-compose.yml" exec -T postgres pg_isready -U tca >/dev/null 2>&1; then
      break
    fi
    sleep 2
    i=$((i + 1))
  done
  log "Running database migrations..."
  cd "${install_root}"
  DATABASE_URL=postgresql+asyncpg://tca:tca_secret@127.0.0.1:5433/telegram_cursor_agent \
    "${UV_BIN}" run alembic upgrade head
}

start_docker_stack() {
  local install_root="$1"
  ensure_docker_running
  ensure_disk_space
  log "Starting Docker services (postgres, redis, bot)..."
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    return 0
  fi
  cd "${install_root}"
  set -a
  # shellcheck disable=SC1091
  source "${install_root}/.env"
  set +a
  docker compose up -d --build
}

install_worker_service() {
  local install_root="$1"
  local unit_path="/etc/systemd/system/${WORKER_SERVICE}.service"
  log "Installing systemd worker: ${unit_path}"

  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    return 0
  fi

  cat >"${unit_path}" <<EOF
[Unit]
Description=Telegram Cursor Agent host worker
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${install_root}
EnvironmentFile=${install_root}/.env
Environment=DATABASE_URL=postgresql+asyncpg://tca:tca_secret@127.0.0.1:5433/telegram_cursor_agent
Environment=REDIS_URL=redis://127.0.0.1:6380/0
Environment=CURSOR_CLI_PATH=${CURSOR_CLI}
Environment=PROJECTS_ROOT=${install_root}/workspace
Environment=PROJECT_SEARCH_ROOTS=${install_root}/workspace,${install_root}
Environment=ALLOWED_PROJECT_ROOTS=/
Environment=SANDBOX_OPEN=true
Environment=SELF_DEPLOY_ENABLED=true
Environment=SELF_REPO_ROOT=${install_root}
Environment=AGENT_WORKSPACE=/
Environment=DEPLOY_SCRIPT=${install_root}/scripts/deploy-self.sh
Environment=UPLOAD_STORAGE_PATH=${install_root}/data/uploads
Environment=CURSOR_MCP_CONFIG_PATH=${HOME}/.cursor/mcp.json
Environment=CURSOR_APPROVE_MCPS=true
Environment=CURSOR_ACCOUNTS_FILE=${CURSOR_ACCOUNTS_FILE}
Environment=CURSOR_ACCOUNTS_DIR=${CURSOR_ACCOUNTS_DIR}
Environment=UV_BIN=${UV_BIN}
ExecStart=${UV_BIN} run telegram-cursor-worker
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
  systemctl enable "${WORKER_SERVICE}"
  systemctl restart "${WORKER_SERVICE}"
}

wait_until_ready() {
  local install_root="$1"
  if [[ "${TCA_DRY_RUN}" == "1" ]]; then
    return 0
  fi
  log "Waiting until bot and worker are up..."
  local i=0
  local bot_ok=0
  local worker_ok=0
  while [[ ${i} -lt 40 ]]; do
    if docker compose -f "${install_root}/docker-compose.yml" ps --status running --services 2>/dev/null | grep -qx bot; then
      bot_ok=1
    fi
    if systemctl is-active --quiet "${WORKER_SERVICE}"; then
      worker_ok=1
    fi
    if [[ ${bot_ok} -eq 1 && ${worker_ok} -eq 1 ]]; then
      log "Bot container and worker are running"
      return 0
    fi
    sleep 3
    i=$((i + 1))
  done
  docker compose -f "${install_root}/docker-compose.yml" ps >&2 || true
  docker compose -f "${install_root}/docker-compose.yml" logs --tail 40 bot >&2 || true
  systemctl status "${WORKER_SERVICE}" --no-pager >&2 || true
  die "Бот или worker не поднялись. Логи выше."
}

prepare_directories() {
  local install_root="$1"
  run mkdir -p "${install_root}/workspace" "${install_root}/data/uploads"
  if [[ ! -f "${install_root}/workspace/.gitkeep" ]]; then
    run touch "${install_root}/workspace/.gitkeep"
  fi
}

refresh_models_catalog() {
  local install_root="$1"
  if [[ ! -f "${install_root}/.env" ]]; then
    return 0
  fi
  log "Обновление каталога моделей Cursor…"
  if (
    cd "${install_root}"
    run "${UV_BIN}" run python scripts/refresh_cursor_models.py
  ); then
    log "Каталог моделей обновлён"
  else
    warn "Не удалось обновить каталог моделей. Выполните: cd ${install_root} && uv run python scripts/refresh_cursor_models.py"
  fi
}

print_success() {
  local install_root="$1"
  echo ""
  echo "=============================================="
  echo "  Telegram Cursor Agent установлен"
  echo "=============================================="
  echo ""
  echo "  Каталог:     ${install_root}"
  echo "  Bot token:   настроен"
  echo "  Admin IDs:   ${ADMIN_TELEGRAM_ID:-configured}"
  echo "  Cursor auth: ${CURSOR_AUTH_FILE}"
  echo ""
  echo "  Сервисы:"
  echo "    docker compose ps          — статус бота и БД"
  echo "    systemctl status ${WORKER_SERVICE}  — статус worker"
  echo ""
  echo "  Откройте Telegram и отправьте боту: /start"
  echo ""
}

main() {
  echo ""
  echo "Telegram Cursor Agent — установка v${TCA_VERSION}"
  echo ""

  if [[ "$(id -u)" -ne 0 ]]; then
    die "Нужен root. Команда: curl -fsSL https://raw.githubusercontent.com/danilax9/telegram-cursor-agent/main/install.sh | sudo bash"
  fi

  local existing_root
  existing_root="$(detect_repo_root)"
  local install_root="${TCA_INSTALL_DIR:-${existing_root:-${HOME}/telegram-cursor-agent}}"

  install_system_packages
  install_docker
  install_uv
  install_node_for_mcp
  install_cursor_cli

  clone_or_update_repo "${install_root}"
  assert_install_root "${install_root}"
  REPO_ROOT="${install_root}"

  prepare_directories "${install_root}"

  echo ""
  echo "--- Настройка Telegram ---"
  echo ""

  local bot_token admin_ids
  if [[ -n "${BOT_TOKEN:-}" ]]; then
    bot_token="${BOT_TOKEN}"
  else
    bot_token="$(prompt_secret "Токен Telegram-бота (от @BotFather)")"
  fi
  validate_bot_token "${bot_token}"

  if [[ -n "${ADMIN_TELEGRAM_ID:-}" ]]; then
    admin_ids="${ADMIN_TELEGRAM_ID}"
  else
    admin_ids="$(prompt "Ваш Telegram user ID (узнать: @userinfobot)" "")"
  fi
  validate_telegram_id "${admin_ids}"

  echo ""
  echo "--- Авторизация Cursor ---"
  echo ""
  cursor_login_interactive

  write_accounts_json
  write_env_file "${install_root}" "${bot_token}" "${admin_ids}"
  install_python_package "${install_root}"
  refresh_models_catalog "${install_root}"
  start_docker_stack "${install_root}"
  run_migrations "${install_root}"
  install_worker_service "${install_root}"
  wait_until_ready "${install_root}"

  ADMIN_TELEGRAM_ID="${admin_ids}"
  print_success "${install_root}"
}

main "$@"
