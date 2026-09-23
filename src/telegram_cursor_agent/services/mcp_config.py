"""Read and write Cursor MCP configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.services.mcp_runtime import resolve_mcp_command


class McpConfigError(Exception):
    pass


class McpConfigService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
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

    @staticmethod
    def normalize_definition(definition: dict[str, Any]) -> dict[str, Any]:
        normalized = json.loads(json.dumps(definition))
        command = normalized.get("command")
        if isinstance(command, str):
            normalized["command"] = resolve_mcp_command(command)
        return normalized

    def normalize_data(self, data: dict[str, Any]) -> dict[str, Any]:
        normalized = json.loads(json.dumps(data))
        servers = normalized.get("mcpServers")
        if not isinstance(servers, dict):
            return normalized
        for server_id, definition in list(servers.items()):
            if isinstance(definition, dict):
                servers[server_id] = self.normalize_definition(definition)
        return normalized

    def repair_local_config(self) -> bool:
        """Normalize launcher paths and sync MCP config to Cursor account homes."""
        if not self._path.is_file():
            return False
        data = self.normalize_data(self.load())
        self.save(data)
        return True

    def _account_mcp_paths(self) -> list[Path]:
        accounts_file = self._settings.cursor_accounts_file
        if not accounts_file.is_file():
            return []
        try:
            raw = json.loads(accounts_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return []
        accounts = raw.get("accounts")
        if not isinstance(accounts, list):
            return []
        paths: list[Path] = []
        for item in accounts:
            if not isinstance(item, dict):
                continue
            auth_raw = item.get("auth_file")
            if not isinstance(auth_raw, str) or not auth_raw.strip():
                continue
            auth_file = Path(auth_raw).expanduser()
            home = auth_file.parent.parent.parent
            paths.append(home / ".cursor" / "mcp.json")
        return paths

    def save(self, data: dict[str, Any]) -> None:
        payload = self.normalize_data(data)
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(text, encoding="utf-8")
        self._path.chmod(0o600)
        for mirror_path in self._account_mcp_paths():
            mirror_path.parent.mkdir(parents=True, exist_ok=True)
            mirror_path.write_text(text, encoding="utf-8")
            mirror_path.chmod(0o600)

    def list_server_ids(self) -> list[str]:
        return sorted(self.load()["mcpServers"].keys())

    def get_server(self, server_id: str) -> dict[str, Any] | None:
        servers = self.load()["mcpServers"]
        value = servers.get(server_id)
        return value if isinstance(value, dict) else None

    def upsert_server(self, server_id: str, definition: dict[str, Any]) -> None:
        data = self.load()
        data["mcpServers"][server_id] = self.normalize_definition(definition)
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
