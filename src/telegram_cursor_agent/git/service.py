"""Git operations with safeguards."""

from dataclasses import dataclass

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.execution.permissions import CommandRisk, classify_command
from telegram_cursor_agent.execution.runner import ProcessRunner, RunResult
from telegram_cursor_agent.execution.sandbox import assert_path_allowed


@dataclass
class GitResult:
    stdout: str
    stderr: str
    returncode: int | None
    blocked: bool = False
    block_reason: str = ""


_BLOCKED_GIT_SUBCOMMANDS = frozenset(
    {
        "push",
        "reset",
        "clean",
        "rebase",
        "merge",
        "checkout",
        "branch",
        "tag",
        "stash",
        "cherry-pick",
    }
)


class GitService:
    def __init__(self, settings: Settings, runner: ProcessRunner) -> None:
        self._settings = settings
        self._runner = runner

    def _validate_workspace(self, workspace: str) -> str:
        return str(assert_path_allowed(workspace, self._settings))

    async def status(self, workspace: str) -> GitResult:
        return await self._run_git(["git", "status", "--short", "--branch"], workspace)

    async def diff(self, workspace: str, args: list[str] | None = None) -> GitResult:
        cmd = ["git", "diff", *(args or [])]
        return await self._run_git(cmd, workspace)

    async def log(self, workspace: str, max_count: int = 10) -> GitResult:
        cmd = ["git", "log", f"--max-count={max_count}", "--oneline", "--decorate"]
        return await self._run_git(cmd, workspace)

    async def run_safe(self, workspace: str, git_args: list[str]) -> GitResult:
        if not git_args or git_args[0] != "git":
            git_args = ["git", *git_args]

        subcommand = git_args[1] if len(git_args) > 1 else ""
        if subcommand in _BLOCKED_GIT_SUBCOMMANDS:
            return GitResult(
                stdout="",
                stderr="",
                returncode=None,
                blocked=True,
                block_reason=f"Git subcommand '{subcommand}' requires confirmation",
            )

        command_str = " ".join(git_args)
        classification = classify_command(command_str, self._settings)
        if classification.risk == CommandRisk.FORBIDDEN:
            return GitResult(
                stdout="",
                stderr="",
                returncode=None,
                blocked=True,
                block_reason=classification.reason,
            )

        return await self._run_git(git_args, workspace)

    async def _run_git(self, cmd: list[str], workspace: str) -> GitResult:
        safe_workspace = self._validate_workspace(workspace)
        result: RunResult = await self._runner.run(cmd, cwd=safe_workspace)
        return GitResult(
            stdout=result.stdout,
            stderr=result.stderr,
            returncode=result.returncode,
        )
