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
    task_timeout: int = Field(
        default=600,
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

    @field_validator("log_json", mode="before")
    @classmethod
    def parse_bool(cls, value: object) -> object:
        if isinstance(value, str):
            return value.lower() in ("1", "true", "yes", "on")
        return value

    @field_validator(
        "task_timeout", "cursor_agent_max_output_bytes", "max_upload_bytes", mode="before"
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

    @field_validator("project_search_roots", "allowed_project_roots", mode="before")
    @classmethod
    def parse_path_lists(cls, value: object) -> list[Path]:
        return parse_path_list(value)

    @field_validator("projects_root", "upload_storage_path", "cursor_auth_file", mode="before")
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
        if not self.project_search_roots and not self.allowed_project_roots:
            self.project_search_roots = [self.projects_root]
            self.allowed_project_roots = [self.projects_root]
        elif not self.project_search_roots:
            self.project_search_roots = list(self.allowed_project_roots)
        elif not self.allowed_project_roots:
            self.allowed_project_roots = list(self.project_search_roots)
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
    def project_discovery_roots(self) -> list[Path]:
        return self.allowed_project_roots

    @property
    def cursor_agent_bin(self) -> str:
        return self.cursor_cli_path

    @property
    def cursor_agent_timeout_seconds(self) -> int:
        return self.task_timeout


@lru_cache
def get_settings() -> Settings:
    # Values are supplied by the environment, not constructor arguments.
    return Settings()  # type: ignore[call-arg]


def clear_settings_cache() -> None:
    get_settings.cache_clear()
