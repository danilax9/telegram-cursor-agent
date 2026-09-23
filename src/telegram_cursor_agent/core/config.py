"""Application configuration via Pydantic Settings."""

from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_path_list(value: object) -> list[Path]:
    """Parse comma-separated or list env values into list[Path]."""
    if value is None:
        return []
    if isinstance(value, Path):
        return [value]
    if isinstance(value, str):
        return [Path(item.strip()) for item in value.split(",") if item.strip()]
    if isinstance(value, list):
        return [
            item if isinstance(item, Path) else Path(str(item))
            for item in value
        ]
    msg = f"Cannot parse path list from {type(value)!r}"
    raise TypeError(msg)


def _parse_single_path(value: object) -> Path:
    if isinstance(value, Path):
        return value
    if isinstance(value, str):
        return Path(value)
    msg = f"Cannot parse path from {type(value)!r}"
    raise TypeError(msg)


def default_cursor_skills_dirs() -> list[Path]:
    """Skill library for this bot, not the active Cursor account home.

    Account switching sets HOME to ``~/.cursor-accounts/<name>``. Skills
    installed for telegram-cursor-agent live in the root user's Cursor dir.
    """
    home_skills = Path.home() / ".cursor" / "skills"
    root_skills = Path("/root/.cursor/skills")
    if ".cursor-accounts" in home_skills.parts and root_skills.is_dir():
        return [root_skills]
    if home_skills.is_dir():
        return [home_skills]
    if root_skills.is_dir():
        return [root_skills]
    return [home_skills]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        # Comma-separated lists are part of the documented .env contract.
        # Disable Pydantic's JSON decoding so the validators below own parsing.
        enable_decoding=False,
        extra="ignore",
        populate_by_name=True,
    )

    bot_token: str = Field(
        ...,
        validation_alias=AliasChoices("BOT_TOKEN", "TELEGRAM_BOT_TOKEN"),
    )
    admin_telegram_ids: list[int] = Field(
        default_factory=list,
        validation_alias=AliasChoices("ADMIN_TELEGRAM_ID", "TELEGRAM_ADMIN_IDS"),
    )

    database_url: str = Field(
        default="postgresql+asyncpg://tca:tca_secret@localhost:5432/telegram_cursor_agent",
        validation_alias=AliasChoices("DATABASE_URL"),
    )
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        validation_alias=AliasChoices("REDIS_URL"),
    )

    cursor_cli_path: str = Field(
        default="cursor-agent",
        validation_alias=AliasChoices("CURSOR_CLI_PATH", "CURSOR_AGENT_BIN"),
    )
    cursor_auth_file: Path = Field(
        default=Path.home() / ".config" / "cursor" / "auth.json",
        validation_alias=AliasChoices("CURSOR_AUTH_FILE"),
    )
    cursor_accounts_file: Path = Field(
        default=Path("/root/.cursor-accounts/accounts.json"),
        validation_alias=AliasChoices("CURSOR_ACCOUNTS_FILE"),
    )
    cursor_accounts_dir: Path = Field(
        default=Path("/root/.cursor-accounts"),
        validation_alias=AliasChoices("CURSOR_ACCOUNTS_DIR"),
    )
    cursor_account_login_timeout_seconds: int = Field(
        default=600,
        validation_alias=AliasChoices("CURSOR_ACCOUNT_LOGIN_TIMEOUT_SECONDS"),
    )
    task_timeout: int = Field(
        # Real agent runs routinely pass ten minutes; a short cap killed useful work.
        default=3600,
        validation_alias=AliasChoices("TASK_TIMEOUT", "CURSOR_AGENT_TIMEOUT_SECONDS"),
    )
    cursor_agent_max_output_bytes: int = Field(
        default=65536,
        validation_alias=AliasChoices(
            "CURSOR_AGENT_MAX_OUTPUT_BYTES",
        ),
    )

    projects_root: Path = Field(
        default=Path("/workspace"),
        validation_alias=AliasChoices("PROJECTS_ROOT", "WORKSPACE_BASE"),
    )
    project_search_roots: list[Path] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "PROJECT_SEARCH_ROOTS",
            "PROJECT_DISCOVERY_ROOTS",
        ),
    )
    allowed_project_roots: list[Path] = Field(
        default_factory=list,
        validation_alias=AliasChoices(
            "ALLOWED_PROJECT_ROOTS",
            "PROJECT_DISCOVERY_ROOTS",
        ),
    )

    project_file_signatures: list[str] = Field(
        default_factory=lambda: [
            "package.json",
            "pyproject.toml",
            "Cargo.toml",
            "go.mod",
        ],
        validation_alias=AliasChoices("PROJECT_FILE_SIGNATURES"),
    )

    allowed_command_prefixes: list[str] = Field(
        default_factory=lambda: [
            "git",
            "ls",
            "cat",
            "head",
            "tail",
            "find",
            "grep",
            "python",
            "python3",
            "pip",
        ],
        validation_alias=AliasChoices("ALLOWED_COMMAND_PREFIXES"),
    )
    forbidden_path_segments: list[str] = Field(
        default_factory=lambda: [
            ".git",
            ".env",
            "node_modules",
            "__pycache__",
            ".ssh",
            ".aws",
        ],
        validation_alias=AliasChoices("FORBIDDEN_PATH_SEGMENTS"),
    )
    upload_storage_path: Path = Field(
        default=Path("/data/uploads"),
        validation_alias=AliasChoices("UPLOAD_STORAGE_PATH"),
    )
    upload_host_path: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("UPLOAD_HOST_PATH"),
    )
    max_upload_bytes: int = Field(
        default=10_485_760,
        validation_alias=AliasChoices("MAX_UPLOAD_BYTES"),
    )

    confirmation_ttl_seconds: int = Field(
        default=300,
        validation_alias=AliasChoices("CONFIRMATION_TTL_SECONDS"),
    )

    log_level: str = Field(default="INFO", validation_alias=AliasChoices("LOG_LEVEL"))
    log_json: bool = Field(default=False, validation_alias=AliasChoices("LOG_JSON"))

    app_env: str = Field(default="production", validation_alias=AliasChoices("APP_ENV"))

    sandbox_open: bool = Field(
        default=False,
        validation_alias=AliasChoices("SANDBOX_OPEN"),
    )
    self_deploy_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("SELF_DEPLOY_ENABLED"),
    )
    self_repo_root: Path = Field(
        default=Path("/root/telegram-cursor-agent"),
        validation_alias=AliasChoices("SELF_REPO_ROOT"),
    )
    agent_workspace: Path | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENT_WORKSPACE"),
    )
    deploy_script: Path = Field(
        default=Path("/root/telegram-cursor-agent/scripts/deploy-self.sh"),
        validation_alias=AliasChoices("DEPLOY_SCRIPT"),
    )
    uv_bin: Path = Field(
        default=Path("/root/.hermes/bin/uv"),
        validation_alias=AliasChoices("UV_BIN"),
    )
    worker_service_name: str = Field(
        default="telegram-cursor-agent-worker",
        validation_alias=AliasChoices("WORKER_SERVICE_NAME"),
    )
    bot_compose_service: str = Field(
        default="bot",
        validation_alias=AliasChoices("BOT_COMPOSE_SERVICE"),
    )
    cursor_mcp_config_path: Path = Field(
        default=Path.home() / ".cursor" / "mcp.json",
        validation_alias=AliasChoices("CURSOR_MCP_CONFIG_PATH"),
    )
    cursor_approve_mcps: bool = Field(
        default=True,
        validation_alias=AliasChoices("CURSOR_APPROVE_MCPS"),
    )
    cursor_skills_dirs: list[Path] = Field(
        default_factory=default_cursor_skills_dirs,
        validation_alias=AliasChoices("CURSOR_SKILLS_DIRS"),
    )
    mcp_setup_ttl_seconds: int = Field(
        default=3600,
        validation_alias=AliasChoices("MCP_SETUP_TTL_SECONDS"),
    )
    deploy_recovery_delay_seconds: int = Field(
        default=3,
        validation_alias=AliasChoices("DEPLOY_RECOVERY_DELAY_SECONDS"),
    )
    deploy_worker_ready_key: str = Field(
        default="tca:worker:ready",
        validation_alias=AliasChoices("DEPLOY_WORKER_READY_KEY"),
    )
    deploy_worker_restart_key: str = Field(
        default="tca:worker:restart_pending",
        validation_alias=AliasChoices("DEPLOY_WORKER_RESTART_KEY"),
    )

    @field_validator(
        "sandbox_open",
        "self_deploy_enabled",
        "cursor_approve_mcps",
        mode="before",
    )
    @classmethod
    def parse_bool_flags(cls, value: object) -> object:
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return value

    @field_validator(
        "self_repo_root", "deploy_script", "uv_bin", "cursor_mcp_config_path", mode="before"
    )
    @classmethod
    def parse_self_deploy_paths(cls, value: object) -> Path:
        return _parse_single_path(value)

    @field_validator("log_json", mode="before")
    @classmethod
    def parse_bool(cls, value: object) -> object:
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return value

    @field_validator(
        "task_timeout",
        "cursor_agent_max_output_bytes",
        "cursor_account_login_timeout_seconds",
        "max_upload_bytes",
        mode="before",
    )
    @classmethod
    def parse_int(cls, value: object) -> object:
        if isinstance(value, str) and value.strip():
            return int(value)
        return value

    @field_validator("admin_telegram_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, int):
            return [value]
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        if isinstance(value, list):
            return [int(item) for item in value]
        return value

    @field_validator("upload_host_path", mode="before")
    @classmethod
    def parse_optional_path(cls, value: object) -> object:
        if value is None or value == "":
            return None
        return _parse_single_path(value)

    @field_validator(
        "project_search_roots",
        "allowed_project_roots",
        "cursor_skills_dirs",
        mode="before",
    )
    @classmethod
    def parse_path_lists(cls, value: object) -> list[Path]:
        return parse_path_list(value)

    @field_validator(
        "projects_root",
        "upload_storage_path",
        "cursor_auth_file",
        "cursor_accounts_file",
        "cursor_accounts_dir",
        mode="before",
    )
    @classmethod
    def parse_paths(cls, value: object) -> Path:
        return _parse_single_path(value)

    @field_validator(
        "project_file_signatures",
        "allowed_command_prefixes",
        "forbidden_path_segments",
        mode="before",
    )
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def normalize_roots(self) -> Self:
        if self.sandbox_open:
            self.allowed_project_roots = [Path("/")]
            self.project_search_roots = [Path("/"), self.projects_root]
            if self.agent_workspace is None:
                self.agent_workspace = Path("/")
        elif not self.project_search_roots and not self.allowed_project_roots:
            self.project_search_roots = [self.projects_root]
            self.allowed_project_roots = [self.projects_root]
        elif not self.project_search_roots:
            self.project_search_roots = list(self.allowed_project_roots)
        elif not self.allowed_project_roots:
            self.allowed_project_roots = list(self.project_search_roots)

        if self.self_deploy_enabled:
            repo_root = self.self_repo_root.resolve()
            if repo_root not in self.allowed_project_roots:
                self.allowed_project_roots.append(repo_root)
            if repo_root not in self.project_search_roots:
                self.project_search_roots.append(repo_root)

        return self

    # Backward-compatible accessors used across the codebase.
    @property
    def telegram_bot_token(self) -> str:
        return self.bot_token

    @property
    def telegram_admin_ids(self) -> list[int]:
        return self.admin_telegram_ids

    @property
    def workspace_base(self) -> Path:
        return self.projects_root

    @property
    def agent_upload_storage_path(self) -> Path:
        """Upload directory visible to cursor-agent on the host worker."""
        if self.upload_host_path is not None:
            return self.upload_host_path
        return self.upload_storage_path

    @property
    def effective_projects_root(self) -> Path:
        """Workspace root that exists in the current runtime (host vs Docker)."""
        if self.projects_root.is_dir():
            return self.projects_root
        container_root = Path("/workspace")
        if container_root.is_dir():
            return container_root
        return self.projects_root

    @property
    def project_discovery_roots(self) -> list[Path]:
        return self.allowed_project_roots

    @property
    def cursor_agent_bin(self) -> str:
        return self.cursor_cli_path

    @property
    def cursor_agent_timeout_seconds(self) -> int:
        return self.task_timeout

    @property
    def resolved_agent_workspace(self) -> Path:
        if self.agent_workspace is not None:
            return self.agent_workspace
        return self.projects_root

    def normalize_cursor_workspace(self, value: str | Path | None) -> str:
        """Single canonical path for cursor-agent --workspace (required for --resume)."""
        raw = str(value or self.projects_root).strip()
        if raw == "/workspace":
            raw = str(self.projects_root)
        agent = str(self.resolved_agent_workspace)
        project = str(self.projects_root)
        if self.sandbox_open and agent == "/":
            if raw in {"/", project}:
                return "/"
        if raw == "/":
            return agent
        return raw

    @property
    def effective_allowed_command_prefixes(self) -> list[str]:
        prefixes = list(self.allowed_command_prefixes)
        if not self.self_deploy_enabled:
            return prefixes
        for extra in ("bash", "sh", "systemctl", "docker", "uv", "make", "sudo"):
            if extra not in prefixes:
                prefixes.append(extra)
        return prefixes


@lru_cache
def get_settings() -> Settings:
    # Values are supplied by the environment, not constructor arguments.
    return Settings()  # type: ignore[call-arg]


def clear_settings_cache() -> None:
    get_settings.cache_clear()
