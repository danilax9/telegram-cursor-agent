#!/usr/bin/env bash
# Bootstrap installer — one-liner entry point.
#
#   curl -fsSL https://raw.githubusercontent.com/danilax9/telegram-cursor-agent/main/install.sh | bash
#
# Optional environment variables:
#   TCA_GITHUB_REPO    — owner/repo (default: danilax9/telegram-cursor-agent)
#   TCA_INSTALL_DIR    — install directory (default: ~/telegram-cursor-agent)
#   TCA_REPO_BRANCH    — git branch (default: main)
#   BOT_TOKEN          — skip interactive token prompt
#   ADMIN_TELEGRAM_ID  — skip interactive admin ID prompt
#   TCA_SKIP_CURSOR_LOGIN=1 — reuse existing Cursor auth
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
if [[ -f "$(dirname "${BASH_SOURCE[0]}")/scripts/install.sh" ]]; then
  export TCA_REPO_URL TCA_INSTALL_DIR TCA_REPO_BRANCH
  exec bash "$(dirname "${BASH_SOURCE[0]}")/scripts/install.sh" "$@"
fi

log "Repository: ${TCA_GITHUB_REPO} (branch: ${TCA_REPO_BRANCH})"
log "Install dir: ${TCA_INSTALL_DIR}"

if [[ -d "${TCA_INSTALL_DIR}/.git" ]]; then
  log "Directory exists, updating..."
  git -C "${TCA_INSTALL_DIR}" fetch origin "${TCA_REPO_BRANCH}"
  git -C "${TCA_INSTALL_DIR}" checkout "${TCA_REPO_BRANCH}" 2>/dev/null || true
  git -C "${TCA_INSTALL_DIR}" pull --ff-only
else
  log "Cloning ${TCA_REPO_URL}..."
  mkdir -p "$(dirname "${TCA_INSTALL_DIR}")"
  git clone --branch "${TCA_REPO_BRANCH}" --depth 1 "${TCA_REPO_URL}" "${TCA_INSTALL_DIR}"
fi

export TCA_INSTALL_DIR TCA_REPO_URL TCA_REPO_BRANCH
exec bash "${TCA_INSTALL_DIR}/scripts/install.sh" "$@"
