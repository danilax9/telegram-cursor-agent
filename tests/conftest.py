"""Shared test fixtures."""

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from telegram_cursor_agent.core.config import Settings, clear_settings_cache
from telegram_cursor_agent.database.base import Base
from telegram_cursor_agent.execution.runner import ProcessRunner


@pytest.fixture
def tmp_workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture
def test_settings(tmp_workspace: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Settings:
    clear_settings_cache()
    monkeypatch.setenv("BOT_TOKEN", "test-token")
    monkeypatch.setenv("ADMIN_TELEGRAM_ID", "12345")
    monkeypatch.setenv("CURSOR_CLI_PATH", "cursor-agent")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/15")
    monkeypatch.setenv("ALLOWED_PROJECT_ROOTS", str(tmp_workspace))
    monkeypatch.setenv("PROJECT_SEARCH_ROOTS", str(tmp_workspace))
    monkeypatch.setenv("PROJECTS_ROOT", str(tmp_workspace))
    monkeypatch.setenv("UPLOAD_STORAGE_PATH", str(tmp_path / "uploads"))
    monkeypatch.setenv("SANDBOX_OPEN", "false")
    monkeypatch.setenv("SELF_DEPLOY_ENABLED", "false")
    clear_settings_cache()
    return Settings()


@pytest.fixture
async def db_session(test_settings: Settings) -> AsyncSession:
    engine = create_async_engine(test_settings.database_url, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture
def runner(test_settings: Settings) -> ProcessRunner:
    return ProcessRunner(test_settings)
