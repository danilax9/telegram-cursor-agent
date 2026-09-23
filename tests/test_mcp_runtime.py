"""MCP runtime path resolution tests."""

from pathlib import Path

from telegram_cursor_agent.services.mcp_runtime import resolve_mcp_command


def test_resolve_mcp_command_uses_local_npx_when_present() -> None:
    if Path("/usr/local/bin/npx").is_file():
        assert resolve_mcp_command("npx") == "/usr/local/bin/npx"


def test_resolve_mcp_command_passthrough_custom() -> None:
    assert resolve_mcp_command("/opt/bin/custom-mcp") == "/opt/bin/custom-mcp"
