#!/usr/bin/env bash
# Launchd entrypoint: load .env and start the host worker.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
export PATH="${HOME}/.local/bin:${HOME}/.opencode/bin:/opt/homebrew/bin:/usr/local/bin:${PATH:-/usr/bin:/bin}"
set -a
# shellcheck disable=SC1091
source "${ROOT}/.env"
set +a
if ! docker info >/dev/null 2>&1 && [[ -S "${HOME}/.colima/default/docker.sock" ]]; then
  export DOCKER_HOST="${DOCKER_HOST:-unix://${HOME}/.colima/default/docker.sock}"
fi
exec "${UV_BIN:?UV_BIN is not set}" run telegram-cursor-worker
