#!/usr/bin/env bash
# Uninstall Telegram Cursor Agent from the server.
set -euo pipefail

TCA_INSTALL_DIR="${TCA_INSTALL_DIR:-${HOME}/telegram-cursor-agent}"
WORKER_SERVICE="${WORKER_SERVICE_NAME:-telegram-cursor-agent-worker}"
TCA_REMOVE_DATA="${TCA_REMOVE_DATA:-}"
TCA_REMOVE_INSTALL_DIR="${TCA_REMOVE_INSTALL_DIR:-}"
TCA_REMOVE_CURSOR_AUTH="${TCA_REMOVE_CURSOR_AUTH:-}"
TCA_NONINTERACTIVE="${TCA_NONINTERACTIVE:-0}"
TCA_DRY_RUN="${TCA_DRY_RUN:-0}"

log() {
  echo "[uninstall] $*" >&2
}

warn() {
  echo "[uninstall] WARNING: $*" >&2
}

die() {
  echo "[uninstall] ERROR: $*" >&2
  exit 1
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

confirm() {
  local message="$1"
  local default="${2:-N}"
  if [[ -n "${3:-}" ]]; then
    case "${3}" in
      1|yes|true|YES|TRUE) return 0 ;;
      0|no|false|NO|FALSE) return 1 ;;
    esac
  fi
  if ! is_interactive; then
    [[ "${default}" == "Y" ]]
    return
  fi
  local answer=""
  read_prompt -p "${message} [${default}/$( [[ "${default}" == "Y" ]] && echo n || echo y )]: " answer
  answer="${answer:-${default}}"
  [[ "${answer}" =~ ^[Yy]$ ]]
}

detect_install_dir() {
  if [[ -f "${TCA_INSTALL_DIR}/docker-compose.yml" ]]; then
    echo "${TCA_INSTALL_DIR}"
    return 0
  fi
  if [[ -f "${TCA_INSTALL_DIR}/.env" ]]; then
    echo "${TCA_INSTALL_DIR}"
    return 0
  fi
  echo ""
}

stop_worker_service() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return 0
  fi
  if systemctl list-unit-files "${WORKER_SERVICE}.service" >/dev/null 2>&1; then
    log "Stopping systemd worker ${WORKER_SERVICE}..."
    run systemctl stop "${WORKER_SERVICE}" 2>/dev/null || true
    run systemctl disable "${WORKER_SERVICE}" 2>/dev/null || true
  fi
  local unit="/etc/systemd/system/${WORKER_SERVICE}.service"
  if [[ -f "${unit}" ]]; then
    log "Removing ${unit}"
    run rm -f "${unit}"
    run systemctl daemon-reload
  fi
}

stop_docker_stack() {
  local install_root="$1"
  local remove_volumes="$2"
  if ! command -v docker >/dev/null 2>&1; then
    return 0
  fi
  if [[ ! -f "${install_root}/docker-compose.yml" ]]; then
    return 0
  fi
  log "Stopping Docker services..."
  cd "${install_root}"
  if [[ "${remove_volumes}" == "1" ]]; then
    run docker compose down -v --remove-orphans
  else
    run docker compose down --remove-orphans
  fi
}

remove_install_dir() {
  local install_root="$1"
  log "Removing install directory ${install_root}..."
  run rm -rf "${install_root}"
}

remove_cursor_auth() {
  local auth_file="${CURSOR_AUTH_FILE:-${HOME}/.config/cursor/auth.json}"
  local accounts_dir="${CURSOR_ACCOUNTS_DIR:-${HOME}/.cursor-accounts}"
  log "Removing Cursor auth data..."
  run rm -f "${auth_file}"
  run rm -rf "${accounts_dir}"
}

print_done() {
  echo ""
  echo "=============================================="
  echo "  Telegram Cursor Agent удалён"
  echo "=============================================="
  echo ""
}

main() {
  echo ""
  echo "Telegram Cursor Agent — удаление"
  echo ""

  local install_root
  install_root="$(detect_install_dir)"
  if [[ -z "${install_root}" ]]; then
    die "Installation not found. Set TCA_INSTALL_DIR to the install path."
  fi

  log "Install directory: ${install_root}"

  if [[ -f "${install_root}/.env" ]]; then
    # shellcheck disable=SC1091
    set -a
    source "${install_root}/.env"
    set +a
    WORKER_SERVICE="${WORKER_SERVICE_NAME:-${WORKER_SERVICE}}"
  fi

  local remove_data="${TCA_REMOVE_DATA}"
  if [[ -z "${remove_data}" ]]; then
    if confirm "Удалить Docker volumes (PostgreSQL, Redis данные)?" "N" "${TCA_REMOVE_DATA}"; then
      remove_data="1"
    else
      remove_data="0"
    fi
  fi

  local remove_dir="${TCA_REMOVE_INSTALL_DIR}"
  if [[ -z "${remove_dir}" ]]; then
    if confirm "Удалить каталог ${install_root}?" "Y" "${TCA_REMOVE_INSTALL_DIR}"; then
      remove_dir="1"
    else
      remove_dir="0"
    fi
  fi

  local remove_auth="${TCA_REMOVE_CURSOR_AUTH}"
  if [[ -z "${remove_auth}" ]]; then
    if confirm "Удалить Cursor auth и accounts.json?" "N" "${TCA_REMOVE_CURSOR_AUTH}"; then
      remove_auth="1"
    else
      remove_auth="0"
    fi
  fi

  stop_worker_service
  stop_docker_stack "${install_root}" "${remove_data}"

  if [[ "${remove_dir}" == "1" ]]; then
    remove_install_dir "${install_root}"
  else
    warn "Каталог ${install_root} сохранён"
  fi

  if [[ "${remove_auth}" == "1" ]]; then
    remove_cursor_auth
  fi

  print_done
}

main "$@"
