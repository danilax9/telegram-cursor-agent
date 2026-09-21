"""Detached self-deploy for bot + worker restarts."""

import asyncio
import os
from pathlib import Path

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.services.deploy_resume import (
    DEPLOY_SUCCESS_MESSAGE,
    DeployContext,
    context_to_env,
    update_marker_completed,
    write_marker,
)


class DeployService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _resolve_script(self) -> Path:
        script = self._settings.deploy_script
        if not script.is_file():
            msg = f"Deploy script not found: {script}"
            raise FileNotFoundError(msg)
        return script

    async def trigger(self, context: DeployContext | None = None) -> str:
        if not self._settings.self_deploy_enabled:
            raise PermissionError("Self-deploy is disabled.")

        script = self._resolve_script()
        deploy_context = context or DeployContext()
        write_marker(self._settings, deploy_context, status="started")

        env = os.environ.copy()
        env.update(
            {
                "SELF_REPO_ROOT": str(self._settings.self_repo_root),
                "WORKER_SERVICE_NAME": self._settings.worker_service_name,
                "BOT_COMPOSE_SERVICE": self._settings.bot_compose_service,
            }
        )
        env.update(context_to_env(deploy_context))

        process = await asyncio.create_subprocess_exec(
            "bash",
            str(script),
            cwd=str(self._settings.self_repo_root),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode("utf-8", errors="replace").strip()
        error = stderr.decode("utf-8", errors="replace").strip()

        if process.returncode != 0:
            detail = error or output or f"exit {process.returncode}"
            raise RuntimeError(detail)

        update_marker_completed(self._settings)
        return DEPLOY_SUCCESS_MESSAGE
