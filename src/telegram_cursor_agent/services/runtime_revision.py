"""Detect when on-disk package sources no longer match the running process."""

from __future__ import annotations

import hashlib
import os
import sys
from pathlib import Path

RELOAD_FLAG_FILENAME = ".worker-reload"

_loaded_revision: str | None = None


def package_dir() -> Path:
    import telegram_cursor_agent

    return Path(telegram_cursor_agent.__file__).resolve().parent


def reload_flag_path(repo_root: Path) -> Path:
    return repo_root / RELOAD_FLAG_FILENAME


def source_revision(root: Path | None = None) -> str:
    """Stable hash of the installed package sources."""
    directory = root if root is not None else package_dir()
    digest = hashlib.sha256()
    files = sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)
    for path in files:
        digest.update(path.relative_to(directory).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def capture_loaded_revision(root: Path | None = None) -> str:
    global _loaded_revision
    _loaded_revision = source_revision(root)
    return _loaded_revision


def revision_drifted(root: Path | None = None) -> bool:
    if _loaded_revision is None:
        return False
    return source_revision(root) != _loaded_revision


def should_reload_process(
    *,
    drifted: bool,
    flag_mtime: float | None,
    started_at: float,
    redis_pending: bool,
) -> bool:
    """True when this process must re-exec before serving another task."""
    if drifted or redis_pending:
        return True
    return flag_mtime is not None and flag_mtime > started_at


def reexec_current_process() -> None:
    """Replace this interpreter with a fresh one running the same argv."""
    os.execv(sys.executable, [sys.executable, *sys.argv])
