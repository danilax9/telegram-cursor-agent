"""Resolve MCP launcher binaries on the host."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def resolve_mcp_command(command: str) -> str:
    """Return an executable path for MCP launchers (npx/npm)."""
    normalized = command.strip()
    if normalized not in {"npx", "npm"}:
        return normalized
    for candidate in (
        Path("/usr/local/bin") / normalized,
        Path.home() / ".local/bin" / normalized,
        Path("/usr/bin") / normalized,
    ):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    found = shutil.which(normalized)
    return found or normalized
