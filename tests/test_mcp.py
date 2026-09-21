"""MCP setup and config tests."""

import json
import uuid
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner, RunResult
from telegram_cursor_agent.services.mcp_config import McpConfigService
from telegram_cursor_agent.services.mcp_research import McpResearchService
from telegram_cursor_agent.services.mcp_setup import McpSetupService


@pytest.fixture
def mcp_settings(test_settings, tmp_path, monkeypatch):
    mcp_path = tmp_path / "mcp.json"
    monkeypatch.setenv("CURSOR_MCP_CONFIG_PATH", str(mcp_path))
    monkeypatch.setenv("CURSOR_APPROVE_MCPS", "true")
    from telegram_cursor_agent.core.config import clear_settings_cache, get_settings

    clear_settings_cache()
    return get_settings()


async def test_research_known_mcp() -> None:
    service = McpResearchService()
    result = await service.research("github")
    assert result.server_id == "github"
    assert result.secrets
    assert result.secrets[0].name == "GITHUB_PERSONAL_ACCESS_TOKEN"


def test_mcp_config_upsert(mcp_settings) -> None:
    config = McpConfigService(mcp_settings)
    config.upsert_server(
        "github",
        {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github", "--stdio"],
            "env": {"GITHUB_PERSONAL_ACCESS_TOKEN": "ghp_secret"},
        },
    )
    loaded = config.get_server("github")
    assert loaded is not None
    assert loaded["env"]["GITHUB_PERSONAL_ACCESS_TOKEN"] == "ghp_secret"
    redacted = config.redact_definition(loaded)
    assert "ghp_secret" not in json.dumps(redacted)


async def test_mcp_setup_start_add(db_session, mcp_settings, runner) -> None:
    user = await UserRepository(db_session).upsert(
        telegram_id=4242, username="admin", is_admin=True
    )
    service = McpSetupService(db_session, mcp_settings, runner)
    message, confirmation_id = await service.start_add(user.id, "memory")
    assert confirmation_id is not None
    assert "Memory" in message
    assert "ключи не нужны" in message.lower() or "Ключи не нужны" in message


async def test_mcp_setup_install_without_secrets(
    db_session, mcp_settings, monkeypatch
) -> None:
    user = await UserRepository(db_session).upsert(
        telegram_id=5252, username="admin", is_admin=True
    )
    runner = ProcessRunner(mcp_settings)

    async def fake_run(command, **kwargs):
        return RunResult(stdout="ok", stderr="", returncode=0, cancelled=False)

    monkeypatch.setattr(runner, "run", fake_run)

    service = McpSetupService(db_session, mcp_settings, runner)
    message, confirmation_id = await service.start_add(user.id, "memory")
    assert confirmation_id is not None
    await db_session.commit()
    result = await service.install_from_confirmation(user.id, confirmation_id)
    assert "добавлен" in result.lower()

    config = McpConfigService(mcp_settings)
    assert config.get_server("memory") is not None
