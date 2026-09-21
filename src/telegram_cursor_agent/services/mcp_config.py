"""Read and write Cursor MCP configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from telegram_cursor_agent.core.config import Settings


class McpConfigError(Exception):
    pass


class McpConfigService:
    def __init__(self, settings: Settings) -> None:
        self._path = settings.cursor_mcp_config_path

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, Any]:
        if not self._path.is_file():
            return {"mcpServers": {}}
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise McpConfigError(f"Cannot read MCP config: {exc}") from exc
        if not isinstance(raw, dict):
            raise McpConfigError("MCP config must be a JSON object.")
        raw.setdefault("mcpServers", {})
        if not isinstance(raw["mcpServers"], dict):
            raise McpConfigError("mcpServers must be an object.")
        return raw

    def save(self, data: dict[str, Any]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
        self._path.chmod(0o600)

    def list_server_ids(self) -> list[str]:
        return sorted(self.load()["mcpServers"].keys())

    def get_server(self, server_id: str) -> dict[str, Any] | None:
        servers = self.load()["mcpServers"]
        value = servers.get(server_id)
        return value if isinstance(value, dict) else None

    def upsert_server(self, server_id: str, definition: dict[str, Any]) -> None:
        data = self.load()
        data["mcpServers"][server_id] = definition
        self.save(data)

    def remove_server(self, server_id: str) -> bool:
        data = self.load()
        if server_id not in data["mcpServers"]:
            return False
        del data["mcpServers"][server_id]
        self.save(data)
        return True

    @staticmethod
    def mask_secret(value: str) -> str:
        stripped = value.strip()
        if len(stripped) <= 8:
            return "***"
        return f"{stripped[:4]}…{stripped[-4:]}"

    @staticmethod
    def redact_definition(definition: dict[str, Any]) -> dict[str, Any]:
        redacted = json.loads(json.dumps(definition))
        env = redacted.get("env")
        if isinstance(env, dict):
            redacted["env"] = {
                key: McpConfigService.mask_secret(str(val))
                for key, val in env.items()
            }
        headers = redacted.get("headers")
        if isinstance(headers, dict):
            redacted["headers"] = {
                key: McpConfigService.mask_secret(str(val))
                for key, val in headers.items()
            }
        return redacted
