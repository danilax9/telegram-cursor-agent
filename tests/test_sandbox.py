"""Path traversal and sandbox tests."""

from pathlib import Path

import pytest

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.execution.sandbox import (
    PathAccessError,
    assert_path_allowed,
    canonicalize_path,
    is_path_allowed,
)


def test_canonicalize_within_root(tmp_workspace: Path, test_settings: Settings) -> None:
    roots = [tmp_workspace]
    result = canonicalize_path(tmp_workspace / "project", roots)
    assert result.is_relative_to(tmp_workspace.resolve())


def test_rejects_path_traversal(tmp_workspace: Path, test_settings: Settings) -> None:
    roots = [tmp_workspace]
    with pytest.raises(PathAccessError):
        canonicalize_path("/etc/passwd", roots)


def test_rejects_parent_escape(tmp_workspace: Path, test_settings: Settings) -> None:
    roots = [tmp_workspace]
    with pytest.raises(PathAccessError):
        canonicalize_path(tmp_workspace / ".." / ".." / "etc" / "passwd", roots)


def test_relative_path_resolves_to_first_root(tmp_workspace: Path) -> None:
    roots = [tmp_workspace]
    result = canonicalize_path("subdir", roots)
    assert str(result).startswith(str(tmp_workspace.resolve()))


def test_forbidden_segment_blocked(tmp_workspace: Path, test_settings: Settings) -> None:
    secret = tmp_workspace / ".env"
    secret.write_text("SECRET=1")
    assert not is_path_allowed(secret, [tmp_workspace], test_settings.forbidden_path_segments)


def test_assert_path_allowed_raises(tmp_workspace: Path, test_settings: Settings) -> None:
    with pytest.raises(PathAccessError):
        assert_path_allowed("/etc/shadow", test_settings)
