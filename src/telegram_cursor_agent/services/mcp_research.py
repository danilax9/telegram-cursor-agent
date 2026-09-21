"""Discover MCP packages and setup requirements."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx

from telegram_cursor_agent.services.mcp_catalog import MCP_CATALOG, McpSecretRequirement, McpTemplate


@dataclass
class McpResearchResult:
    server_id: str
    title: str
    description: str
    command: str
    args: list[str]
    secrets: list[McpSecretRequirement] = field(default_factory=list)
    oauth: bool = False
    oauth_note: str | None = None
    docs_url: str | None = None
    source: str = "catalog"
    npm_package: str | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["secrets"] = [asdict(secret) for secret in self.secrets]
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> McpResearchResult:
        secrets = [
            McpSecretRequirement(**item)
            for item in data.get("secrets", [])
            if isinstance(item, dict)
        ]
        return cls(
            server_id=str(data["server_id"]),
            title=str(data.get("title", data["server_id"])),
            description=str(data.get("description", "")),
            command=str(data.get("command", "npx")),
            args=[str(arg) for arg in data.get("args", [])],
            secrets=secrets,
            oauth=bool(data.get("oauth")),
            oauth_note=data.get("oauth_note"),
            docs_url=data.get("docs_url"),
            source=str(data.get("source", "catalog")),
            npm_package=data.get("npm_package"),
        )

    def build_definition(self, env: dict[str, str]) -> dict[str, Any]:
        definition: dict[str, Any] = {
            "command": self.command,
            "args": self.args,
        }
        if env:
            definition["env"] = env
        return definition

    def missing_secrets(self, collected: dict[str, str]) -> list[McpSecretRequirement]:
        return [secret for secret in self.secrets if not collected.get(secret.name, "").strip()]


class McpResearchService:
    NPM_SEARCH_URL = "https://registry.npmjs.org/-/v1/search"

    async def research(self, query: str) -> McpResearchResult:
        normalized = self._normalize_query(query)
        if not normalized:
            raise ValueError("Укажи название MCP, например: github, figma, brave-search.")

        catalog = MCP_CATALOG.get(normalized)
        if catalog is not None:
            return self._from_template(catalog, source="catalog")

        npm_result = await self._search_npm(normalized)
        if npm_result is not None:
            return npm_result

        fallback_package = self._guess_package_name(normalized)
        return McpResearchResult(
            server_id=self._server_id_from_query(normalized),
            title=normalized,
            description=(
                "Пакет не найден в каталоге. Проверь README на npm/GitHub — "
                "возможно, нужны переменные окружения."
            ),
            command="npx",
            args=["-y", fallback_package, "--stdio"],
            source="guess",
            npm_package=fallback_package,
            docs_url=f"https://www.npmjs.com/package/{fallback_package}",
        )

    def _from_template(self, template: McpTemplate, *, source: str) -> McpResearchResult:
        package = self._package_from_args(template.args)
        return McpResearchResult(
            server_id=template.server_id,
            title=template.title,
            description=template.description,
            command=template.command,
            args=list(template.args),
            secrets=list(template.secrets),
            oauth=template.oauth,
            oauth_note=template.oauth_note,
            docs_url=template.docs_url,
            source=source,
            npm_package=package,
        )

    async def _search_npm(self, query: str) -> McpResearchResult | None:
        params = {"text": f"mcp {query}", "size": 8}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(self.NPM_SEARCH_URL, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError:
            return None

        objects = payload.get("objects", [])
        if not isinstance(objects, list):
            return None

        for item in objects:
            package = item.get("package", {}) if isinstance(item, dict) else {}
            if not isinstance(package, dict):
                continue
            name = str(package.get("name", ""))
            if not self._looks_like_mcp_package(name, query):
                continue
            description = str(package.get("description", "") or "")
            links = package.get("links", {})
            docs_url = None
            if isinstance(links, dict):
                docs_url = links.get("npm") or links.get("homepage")
            return McpResearchResult(
                server_id=self._server_id_from_package(name),
                title=name,
                description=description or "MCP server from npm.",
                command="npx",
                args=["-y", name, "--stdio"],
                source="npm",
                npm_package=name,
                docs_url=str(docs_url) if docs_url else f"https://www.npmjs.com/package/{name}",
            )
        return None

    @staticmethod
    def _normalize_query(query: str) -> str:
        value = query.strip().lower()
        value = re.sub(r"^mcp[\s:/-]+", "", value)
        value = re.sub(r"^(add|install|setup|подключи|добавь)\s+", "", value)
        value = value.replace("_", "-")
        return value.strip()

    @staticmethod
    def _server_id_from_query(query: str) -> str:
        cleaned = re.sub(r"[^a-z0-9-]+", "-", query.lower()).strip("-")
        return cleaned or "mcp"

    @staticmethod
    def _server_id_from_package(package: str) -> str:
        name = package.split("/")[-1]
        for prefix in ("server-", "mcp-server-", "mcp-"):
            if name.startswith(prefix):
                name = name[len(prefix) :]
        return McpResearchService._server_id_from_query(name)

    @staticmethod
    def _guess_package_name(query: str) -> str:
        if query.startswith("@") or "/" in query:
            return query
        return f"@modelcontextprotocol/server-{query}"

    @staticmethod
    def _package_from_args(args: list[str]) -> str | None:
        for index, arg in enumerate(args):
            if arg == "-y" and index + 1 < len(args):
                return args[index + 1]
            if arg.startswith("@") or ("/" in arg and not arg.startswith("-")):
                return arg
        return None

    @staticmethod
    def _looks_like_mcp_package(name: str, query: str) -> bool:
        lowered = name.lower()
        if "mcp" not in lowered and "modelcontextprotocol" not in lowered:
            return False
        tokens = [token for token in re.split(r"[^a-z0-9]+", query.lower()) if token]
        if not tokens:
            return True
        return any(token in lowered for token in tokens)
