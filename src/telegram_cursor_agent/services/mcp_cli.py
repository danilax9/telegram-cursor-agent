"""Cursor CLI wrappers for MCP management."""

from __future__ import annotations

import shutil
from pathlib import Path

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.execution.runner import ProcessRunner


class McpCliError(Exception):
    pass


class McpCliService:
    def __init__(self, settings: Settings, runner: ProcessRunner) -> None:
        self._settings = settings
        self._runner = runner

    def is_available(self) -> bool:
        cli = self._settings.cursor_agent_bin
        if Path(cli).is_file():
            return True
        return shutil.which(cli) is not None

    async def list_servers(self) -> str:
        return await self._run("list")

    async def list_tools(self, server_id: str) -> str:
        return await self._run("list-tools", server_id)

    async def enable(self, server_id: str) -> str:
        return await self._run("enable", server_id)

    async def disable(self, server_id: str) -> str:
        return await self._run("disable", server_id)

    async def login(self, server_id: str) -> str:
        return await self._run("login", server_id)

    async def verify_server(self, server_id: str) -> str:
        lines: list[str] = []
        try:
            await self.enable(server_id)
            lines.append("approve: ok")
        except McpCliError as exc:
            lines.append(f"approve: {exc}")
        try:
            tools = await self.list_tools(server_id)
            lines.append(tools)
        except McpCliError as exc:
            lines.append(f"tools: {exc}")
        return "\n".join(lines)

    async def _run(self, *args: str) -> str:
        if not self.is_available():
            raise McpCliError(
                f"Cursor CLI not found: {self._settings.cursor_agent_bin}"
            )
        command = [self._settings.cursor_agent_bin, "mcp", *args]
        try:
            result = await self._runner.run(command)
        except FileNotFoundError as exc:
            raise McpCliError(str(exc)) from exc
        output = result.stdout.strip()
        if result.stderr.strip():
            output = f"{output}\n{result.stderr.strip()}".strip() if output else result.stderr.strip()
        if result.returncode != 0:
            detail = output or f"exit {result.returncode}"
            raise McpCliError(detail)
        return output or "(no output)"
