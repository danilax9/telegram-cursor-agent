#!/usr/bin/env bash
# Bootstrap uninstaller — one-liner entry point.
#
#   curl -fsSL https://raw.githubusercontent.com/danilax9/telegram-cursor-agent/main/uninstall.sh | bash
#
set -euo pipefail

TCA_GITHUB_REPO="${TCA_GITHUB_REPO:-danilax9/telegram-cursor-agent}"
TCA_REPO_BRANCH="${TCA_REPO_BRANCH:-main}"
TCA_INSTALL_DIR="${TCA_INSTALL_DIR:-${HOME}/telegram-cursor-agent}"
TCA_RAW_BASE="https://raw.githubusercontent.com/${TCA_GITHUB_REPO}/${TCA_REPO_BRANCH}"

if [[ -f "$(dirname "${BASH_SOURCE[0]}")/scripts/uninstall.sh" ]]; then
  exec bash "$(dirname "${BASH_SOURCE[0]}")/scripts/uninstall.sh" "$@"
fi

tmp_script="$(mktemp)"
trap 'rm -f "${tmp_script}"' EXIT
curl -fsSL "${TCA_RAW_BASE}/scripts/uninstall.sh" -o "${tmp_script}"
export TCA_INSTALL_DIR
exec bash "${tmp_script}" "$@"
