#!/usr/bin/env bash
# Bootstrap installer. One command:
#
#   Ubuntu:  curl -fsSL https://raw.githubusercontent.com/danilax9/telegram-cursor-agent/main/install.sh | sudo bash
#   macOS:   curl -fsSL https://raw.githubusercontent.com/danilax9/telegram-cursor-agent/main/install.sh | bash
#
# OpenCode with Big Pickle is ready immediately. Cursor login is not asked;
# it can be done later in the bot.
#
# Optional environment variables:
#   TCA_GITHUB_REPO    — owner/repo (default: danilax9/telegram-cursor-agent)
#   TCA_INSTALL_DIR    — install directory (default: ~/telegram-cursor-agent)
#   TCA_REPO_BRANCH    — git branch (default: main)
#   BOT_TOKEN          — skip interactive token prompt
#   ADMIN_TELEGRAM_ID  — skip interactive admin ID prompt
#   TCA_CURSOR_LOGIN=1 — also run Cursor login during install
#   TCA_NONINTERACTIVE=1    — fail instead of prompting
#
set -euo pipefail

TCA_GITHUB_REPO="${TCA_GITHUB_REPO:-danilax9/telegram-cursor-agent}"
TCA_REPO_BRANCH="${TCA_REPO_BRANCH:-main}"
TCA_REPO_URL="${TCA_REPO_URL:-https://github.com/${TCA_GITHUB_REPO}.git}"
TCA_INSTALL_DIR="${TCA_INSTALL_DIR:-${HOME}/telegram-cursor-agent}"

log() {
  echo "[bootstrap] $*" >&2
}

# When executed from a cloned repo, run the full installer directly.
# BASH_SOURCE is unset when the script is piped: curl | bash.
_bootstrap_self="${BASH_SOURCE[0]:-}"
if [[ -n "${_bootstrap_self}" && -f "$(dirname "${_bootstrap_self}")/scripts/install.sh" ]]; then
  export TCA_REPO_URL TCA_INSTALL_DIR TCA_REPO_BRANCH
  exec bash "$(dirname "${_bootstrap_self}")/scripts/install.sh" "$@"
fi

log "Repository: ${TCA_GITHUB_REPO} (branch: ${TCA_REPO_BRANCH})"
log "Install dir: ${TCA_INSTALL_DIR}"

if ! command -v git >/dev/null 2>&1; then
  log "Installing git..."
  if command -v apt-get >/dev/null 2>&1; then
    apt-get update -qq
    apt-get install -y -qq git ca-certificates
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y git ca-certificates
  elif command -v yum >/dev/null 2>&1; then
    yum install -y git ca-certificates
  else
    log "git is not installed and there is no apt/dnf/yum"
    exit 1
  fi
fi

if [[ -d "${TCA_INSTALL_DIR}/.git" ]]; then
  log "Directory exists, updating..."
  git -C "${TCA_INSTALL_DIR}" fetch origin "${TCA_REPO_BRANCH}" >&2
  git -C "${TCA_INSTALL_DIR}" checkout "${TCA_REPO_BRANCH}" >&2 2>/dev/null || true
  git -C "${TCA_INSTALL_DIR}" pull --ff-only >&2
else
  log "Cloning ${TCA_REPO_URL}..."
  mkdir -p "$(dirname "${TCA_INSTALL_DIR}")"
  git clone --branch "${TCA_REPO_BRANCH}" --depth 1 "${TCA_REPO_URL}" "${TCA_INSTALL_DIR}" >&2
fi

export TCA_INSTALL_DIR TCA_REPO_URL TCA_REPO_BRANCH
exec bash "${TCA_INSTALL_DIR}/scripts/install.sh" "$@"
