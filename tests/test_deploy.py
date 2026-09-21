"""Self-deploy service tests."""

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from telegram_cursor_agent.services.deploy import DeployService
from telegram_cursor_agent.services.deploy_resume import DEPLOY_SUCCESS_MESSAGE


@pytest.fixture
def deploy_script(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    script = tmp_path / "deploy-self.sh"
    script.write_text("#!/usr/bin/env bash\necho deployed\n")
    script.chmod(0o755)
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("SELF_DEPLOY_ENABLED", "true")
    monkeypatch.setenv("SELF_REPO_ROOT", str(tmp_path))
    monkeypatch.setenv("DEPLOY_SCRIPT", str(script))
    from telegram_cursor_agent.core.config import clear_settings_cache

    clear_settings_cache()
    return script


async def test_trigger_runs_deploy_script(deploy_script: Path) -> None:
    from telegram_cursor_agent.core.config import get_settings

    settings = get_settings()
    service = DeployService(settings)

    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock) as mock_exec:
        process = AsyncMock()
        process.communicate = AsyncMock(return_value=(b"deployed\n", b""))
        process.returncode = 0
        mock_exec.return_value = process

        result = await service.trigger()

    assert result == DEPLOY_SUCCESS_MESSAGE
    mock_exec.assert_awaited_once()


async def test_trigger_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOT_TOKEN", "token")
    monkeypatch.setenv("SELF_DEPLOY_ENABLED", "false")
    from telegram_cursor_agent.core.config import clear_settings_cache, get_settings

    clear_settings_cache()
    service = DeployService(get_settings())

    with pytest.raises(PermissionError, match="disabled"):
        await service.trigger()
