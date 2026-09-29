#!/usr/bin/env bash
# Launchd entrypoint: load .env and start the host worker.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"
set -a
# shellcheck disable=SC1091
source "${ROOT}/.env"
set +a
exec "${UV_BIN:?UV_BIN is not set}" run telegram-cursor-worker
