"""Git service tests."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from telegram_cursor_agent.execution.runner import RunResult
from telegram_cursor_agent.git.service import GitService


@pytest.fixture
def git_repo(tmp_workspace: Path) -> Path:
    repo = tmp_workspace / "repo"
    repo.mkdir()
    return repo


async def test_git_status(git_repo: Path, test_settings, runner) -> None:
    service = GitService(test_settings, runner)
    with patch.object(runner, "run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = RunResult(returncode=0, stdout="## main", stderr="")
        result = await service.status(str(git_repo))
        assert result.stdout == "## main"
        mock_run.assert_called_once()
        args = mock_run.call_args
        assert args[0][0] == ["git", "status", "--short", "--branch"]


async def test_blocked_git_push(git_repo: Path, test_settings, runner) -> None:
    service = GitService(test_settings, runner)
    result = await service.run_safe(str(git_repo), ["git", "push", "origin", "main"])
    assert result.blocked is True
    assert "confirmation" in result.block_reason


async def test_git_log(git_repo: Path, test_settings, runner) -> None:
    service = GitService(test_settings, runner)
    with patch.object(runner, "run", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = RunResult(returncode=0, stdout="abc123 init", stderr="")
        result = await service.log(str(git_repo))
        assert "abc123" in result.stdout
