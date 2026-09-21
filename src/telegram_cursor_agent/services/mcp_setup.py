"""Interactive MCP setup flow for Telegram."""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.repositories.confirmation import ConfirmationRepository
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.mcp_catalog import McpSecretRequirement
from telegram_cursor_agent.services.mcp_cli import McpCliError, McpCliService
from telegram_cursor_agent.services.mcp_config import McpConfigService
from telegram_cursor_agent.services.mcp_research import McpResearchResult, McpResearchService
from telegram_cursor_agent.telegram.markdown import md_bold, md_code, md_code_block, md_italic, md_link

MCP_SETUP_ACTION = "mcp_setup"


@dataclass
class McpSetupState:
    stage: str
    result: McpResearchResult
    collected: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "result": self.result.to_dict(),
            "collected": self.collected,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> McpSetupState:
        return cls(
            stage=str(data.get("stage", "awaiting_secrets")),
            result=McpResearchResult.from_dict(data["result"]),
            collected={
                str(key): str(value)
                for key, value in (data.get("collected") or {}).items()
            },
        )


class McpSetupService:
    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        runner: ProcessRunner,
        task_queue: TaskQueue | None = None,
    ) -> None:
        self._db = db
        self._settings = settings
        self._runner = runner
        self._task_queue = task_queue
        self._confirmations = ConfirmationRepository(db)
        self._tasks = TaskRepository(db)
        self._research = McpResearchService()
        self._config = McpConfigService(settings)
        self._cli = McpCliService(settings, runner)

    async def start_add(self, user_id: uuid.UUID, query: str) -> tuple[str, uuid.UUID | None]:
        await self._cancel_pending(user_id)
        result = await self._research.research(query)
        state = McpSetupState(
            stage="awaiting_secrets" if result.secrets else "ready",
            result=result,
            collected={},
        )
        confirmation = await self._confirmations.create(
            user_id=user_id,
            action_type=MCP_SETUP_ACTION,
            action_payload=json.dumps(state.to_dict()),
            ttl_seconds=self._settings.mcp_setup_ttl_seconds,
        )
        return self._format_preview(state), confirmation.id

    async def try_handle_pending_message(
        self, user_id: uuid.UUID, text: str
    ) -> str | None:
        confirmation = await self._confirmations.get_pending_by_type(
            user_id, MCP_SETUP_ACTION
        )
        if confirmation is None:
            return None

        state = McpSetupState.from_dict(json.loads(confirmation.action_payload))
        if state.stage not in {"awaiting_secrets", "ready"}:
            return None

        parsed = self._parse_secret_input(text, state.result.secrets)
        if not parsed and state.result.secrets:
            return (
                "Не понял ключи. Отправь в формате:\n"
                "`ИМЯ_ПЕРЕМЕННОЙ=значение`\n"
                "Можно несколько строк."
            )

        state.collected.update(parsed)
        missing = state.result.missing_secrets(state.collected)
        if missing:
            state.stage = "awaiting_secrets"
            await self._save_state(confirmation.id, state)
            return self._format_missing_secrets(state, missing)

        return await self._install(user_id, confirmation.id, state)

    async def install_from_confirmation(
        self, user_id: uuid.UUID, confirmation_id: uuid.UUID
    ) -> str:
        confirmation = await self._confirmations.get_by_id(confirmation_id)
        if confirmation is None or confirmation.user_id != user_id:
            return "Сессия установки MCP не найдена или истекла."
        if confirmation.action_type != MCP_SETUP_ACTION:
            return "Неверный тип подтверждения."
        state = McpSetupState.from_dict(json.loads(confirmation.action_payload))
        missing = state.result.missing_secrets(state.collected)
        if missing:
            state.stage = "awaiting_secrets"
            await self._save_state(confirmation.id, state)
            return self._format_missing_secrets(state, missing)
        return await self._install(user_id, confirmation.id, state)

    async def cancel(self, user_id: uuid.UUID, confirmation_id: uuid.UUID) -> str:
        confirmation = await self._confirmations.get_by_id(confirmation_id)
        if confirmation is None or confirmation.user_id != user_id:
            return "Уже отменено."
        await self._confirmations.reject(confirmation_id)
        return "Установка MCP отменена."

    async def list_servers(self) -> str:
        configured = self._config.list_server_ids()
        lines = [md_bold("MCP на сервере")]
        if configured:
            names = ", ".join(md_code(name) for name in configured)
            lines.append(f"\n*В конфиге:* {names}")
        else:
            lines.append("\nВ конфиге пока пусто.")

        if self._cli.is_available():
            try:
                cli_output = await self._cli.list_servers()
            except McpCliError as exc:
                cli_output = f"(cli error: {exc})"
            lines.append(f"\n*Статус CLI:*\n{md_code_block(cli_output)}")
        else:
            lines.append(
                f"\n{md_italic('Статус CLI смотрит host worker')} "
                f"({self._settings.cursor_agent_bin})."
            )

        lines.append(
            "\nДобавить: `/mcp add github` или `добавь mcp figma`"
        )
        return "\n".join(lines)

    async def _install(
        self,
        user_id: uuid.UUID,
        confirmation_id: uuid.UUID,
        state: McpSetupState,
    ) -> str:
        result = state.result
        collected = state.collected

        definition = result.build_definition(collected)
        self._config.upsert_server(result.server_id, definition)
        await self._confirmations.approve(confirmation_id)

        redacted = self._config.redact_definition(definition)
        if self._cli.is_available():
            verify_text = await self._cli.verify_server(result.server_id)
        elif self._task_queue is not None:
            await self._queue_finalize(user_id, result.server_id, result.title)
            verify_text = "Проверка запущена на host worker…"
        else:
            verify_text = "Конфиг записан. Проверка будет на host worker."

        config_text = json.dumps(redacted, ensure_ascii=False, indent=2)
        return (
            f"✅ MCP *{result.title}* добавлен как {md_code(result.server_id)}.\n\n"
            f"*Конфиг:*\n{md_code_block(config_text)}\n\n"
            f"*Проверка:*\n{md_code_block(verify_text)}"
        )

    async def finalize_server(self, server_id: str, title: str | None = None) -> str:
        label = title or server_id
        if not self._cli.is_available():
            return f"MCP {md_code(server_id)}: Cursor CLI недоступен на worker."
        verify_text = await self._cli.verify_server(server_id)
        return (
            f"✅ MCP *{label}* ({md_code(server_id)}) проверен на сервере.\n\n"
            f"{md_code_block(verify_text)}"
        )

    async def _queue_finalize(
        self, user_id: uuid.UUID, server_id: str, title: str
    ) -> None:
        if self._task_queue is None:
            return
        task = await self._tasks.create(
            user_id=user_id,
            task_type="mcp_finalize",
            payload=json.dumps({"server_id": server_id, "title": title}),
        )
        await self._task_queue.enqueue(str(task.id))

    async def _cancel_pending(self, user_id: uuid.UUID) -> None:
        pending = await self._confirmations.get_pending_by_type(user_id, MCP_SETUP_ACTION)
        if pending is not None:
            await self._confirmations.reject(pending.id)

    async def _save_state(self, confirmation_id: uuid.UUID, state: McpSetupState) -> None:
        confirmation = await self._confirmations.get_by_id(confirmation_id)
        if confirmation is None:
            return
        confirmation.action_payload = json.dumps(state.to_dict())
        await self._db.flush()

    def _format_preview(self, state: McpSetupState) -> str:
        result = state.result
        cmd = f"{result.command} {' '.join(result.args)}"
        lines = [
            f"*{result.title}* → {md_code(result.server_id)}",
            result.description,
        ]
        if result.docs_url:
            lines.append(f"Документация: {md_link(result.docs_url, result.docs_url)}")
        lines.append(f"\n*Команда:* {md_code(cmd)}")
        if result.secrets:
            lines.append("\n*Нужно от тебя (без SSH, прямо сюда в Telegram):*")
            for secret in result.secrets:
                lines.append(self._format_secret_requirement(secret))
            lines.append(
                "\nОтправь ключи сообщением, например:\n"
                f"`{result.secrets[0].name}=...`"
            )
        else:
            lines.append("\nКлючи не нужны — можно установить сразу.")
        if result.oauth and result.oauth_note:
            lines.append(f"\n*OAuth:* {result.oauth_note}")
        lines.append(f"\nИсточник: {result.source}")
        return "\n".join(lines)

    def _format_missing_secrets(
        self, state: McpSetupState, missing: list[McpSecretRequirement]
    ) -> str:
        lines = ["Принял. Ещё нужны:"]
        for secret in missing:
            lines.append(self._format_secret_requirement(secret))
        lines.append(
            "\nОтправь недостающие значения в формате `ИМЯ=значение`."
        )
        return "\n".join(lines)

    @staticmethod
    def _format_secret_requirement(secret: McpSecretRequirement) -> str:
        line = f"• {md_code(secret.name)} — {secret.label}. {secret.hint}"
        if secret.url:
            line += f" {md_link('получить', secret.url)}"
        return line

    @staticmethod
    def _parse_secret_input(
        text: str, requirements: list[McpSecretRequirement]
    ) -> dict[str, str]:
        collected: dict[str, str] = {}
        known_names = {secret.name for secret in requirements}
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    collected[key] = value
                continue
            if len(requirements) == 1 and "=" not in text and "\n" not in text:
                collected[requirements[0].name] = line
        return {key: value for key, value in collected.items() if key in known_names or not known_names}

