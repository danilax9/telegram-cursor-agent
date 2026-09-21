"""Known MCP server templates."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class McpSecretRequirement:
    name: str
    label: str
    hint: str
    url: str | None = None


@dataclass(frozen=True)
class McpTemplate:
    server_id: str
    title: str
    description: str
    command: str
    args: list[str]
    secrets: list[McpSecretRequirement] = field(default_factory=list)
    oauth: bool = False
    oauth_note: str | None = None
    docs_url: str | None = None
    aliases: tuple[str, ...] = ()


def _tpl(
    server_id: str,
    title: str,
    description: str,
    package: str,
    *,
    secrets: list[McpSecretRequirement] | None = None,
    oauth: bool = False,
    oauth_note: str | None = None,
    docs_url: str | None = None,
    aliases: tuple[str, ...] = (),
    extra_args: list[str] | None = None,
) -> McpTemplate:
    args = ["-y", package]
    if extra_args:
        args.extend(extra_args)
    else:
        args.append("--stdio")
    return McpTemplate(
        server_id=server_id,
        title=title,
        description=description,
        command="npx",
        args=args,
        secrets=secrets or [],
        oauth=oauth,
        oauth_note=oauth_note,
        docs_url=docs_url,
        aliases=aliases,
    )


MCP_CATALOG: dict[str, McpTemplate] = {}


def _register(template: McpTemplate) -> None:
    MCP_CATALOG[template.server_id] = template
    for alias in template.aliases:
        MCP_CATALOG[alias] = template


_register(
    _tpl(
        "github",
        "GitHub",
        "Issues, pull requests, files and search in GitHub repositories.",
        "@modelcontextprotocol/server-github",
        secrets=[
            McpSecretRequirement(
                "GITHUB_PERSONAL_ACCESS_TOKEN",
                "GitHub Personal Access Token",
                "Classic PAT with repo scope or fine-grained token for needed repos.",
                "https://github.com/settings/tokens",
            )
        ],
        docs_url="https://github.com/modelcontextprotocol/servers/tree/main/src/github",
        aliases=("gh", "git"),
    )
)
_register(
    _tpl(
        "figma",
        "Figma",
        "Read Figma files, components and design context.",
        "figma-developer-mcp",
        secrets=[
            McpSecretRequirement(
                "FIGMA_API_KEY",
                "Figma personal access token",
                "Create in Figma → Settings → Security → Personal access tokens.",
                "https://www.figma.com/developers/api#access-tokens",
            )
        ],
        aliases=("fig",),
    )
)
_register(
    _tpl(
        "brave-search",
        "Brave Search",
        "Web search through Brave Search API.",
        "@modelcontextprotocol/server-brave-search",
        secrets=[
            McpSecretRequirement(
                "BRAVE_API_KEY",
                "Brave Search API key",
                "Create a key in the Brave Search API dashboard.",
                "https://brave.com/search/api/",
            )
        ],
        aliases=("brave", "search"),
    )
)
_register(
    _tpl(
        "postgres",
        "PostgreSQL",
        "Query and inspect PostgreSQL databases.",
        "@modelcontextprotocol/server-postgres",
        secrets=[
            McpSecretRequirement(
                "POSTGRES_CONNECTION_STRING",
                "PostgreSQL connection string",
                "Example: postgresql://user:pass@host:5432/dbname",
            )
        ],
        aliases=("postgresql", "pg", "sql"),
    )
)
_register(
    _tpl(
        "slack",
        "Slack",
        "Post messages and read Slack channels.",
        "@modelcontextprotocol/server-slack",
        secrets=[
            McpSecretRequirement(
                "SLACK_BOT_TOKEN",
                "Slack bot token",
                "xoxb-… token from your Slack app.",
                "https://api.slack.com/apps",
            ),
            McpSecretRequirement(
                "SLACK_TEAM_ID",
                "Slack team ID",
                "Workspace ID starting with T…",
            ),
        ],
        aliases=(),
    )
)
_register(
    _tpl(
        "puppeteer",
        "Puppeteer",
        "Browser automation for scraping and testing.",
        "@modelcontextprotocol/server-puppeteer",
        aliases=("browser", "chrome"),
    )
)
_register(
    _tpl(
        "memory",
        "Memory",
        "Persistent key-value memory for the agent.",
        "@modelcontextprotocol/server-memory",
        aliases=("mem",),
    )
)
