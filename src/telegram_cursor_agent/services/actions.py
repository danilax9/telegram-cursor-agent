"""Typed deterministic service actions with safe fallback."""

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.logging import get_logger

from telegram_cursor_agent.agent.parser import IntentType, ParsedIntent, parse_intent
from telegram_cursor_agent.agent.prompts import HELP_TEXT
from telegram_cursor_agent.agent.sessions import SessionService
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.repositories.confirmation import ConfirmationRepository
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.execution.permissions import (
    CommandRisk,
    classify_command,
    requires_confirmation,
)
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.git.service import GitService
from telegram_cursor_agent.projects.service import ProjectService
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.image_attachments import (
    StoredImage,
    build_prompt_with_images,
)
from telegram_cursor_agent.services.mcp_setup import McpSetupService
from telegram_cursor_agent.services.session_execution import (
    SessionExecState,
    SessionExecutionService,
)

logger = get_logger(__name__)


class ActionResultType(StrEnum):
    TEXT = "text"
    CONFIRMATION_REQUIRED = "confirmation_required"
    TASK_QUEUED = "task_queued"
    REDIRECT_REQUESTED = "redirect_requested"
    MCP_SETUP = "mcp_setup"
    ERROR = "error"


@dataclass
class ActionResult:
    result_type: ActionResultType
    message: str
    task_id: uuid.UUID | None = None
    confirmation_id: uuid.UUID | None = None
    typing_indicator: bool = True


ActionHandler = Callable[..., Awaitable[ActionResult]]


class ActionService:
    """Routes parsed intents to typed handlers."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        runner: ProcessRunner,
        task_queue: TaskQueue,
        redis: Redis | None = None,  # type: ignore[type-arg]
    ) -> None:
        self._db = db
        self._settings = settings
        self._runner = runner
        self._task_queue = task_queue
        self._redis = redis
        self._sessions = SessionService(db, settings)
        self._projects = ProjectService(db, settings)
        self._git = GitService(settings, runner)
        self._tasks = TaskRepository(db)
        self._confirmations = ConfirmationRepository(db)

    async def handle_text(
        self,
        user_id: uuid.UUID,
        text: str,
        workspace: str,
        project_id: uuid.UUID | None = None,
        agent_workspace: str | None = None,
        image_attachments: list[StoredImage] | None = None,
    ) -> ActionResult:
        intent = parse_intent(text)
        return await self._dispatch(
            user_id,
            intent,
            workspace,
            project_id,
            agent_workspace or workspace,
            image_attachments=image_attachments,
        )

    async def _dispatch(
        self,
        user_id: uuid.UUID,
        intent: ParsedIntent,
        workspace: str,
        project_id: uuid.UUID | None,
        agent_workspace: str,
        image_attachments: list[StoredImage] | None = None,
    ) -> ActionResult:
        handlers: dict[IntentType, ActionHandler] = {
            IntentType.HELP: self._handle_help,
            IntentType.CANCEL: self._handle_cancel,
            IntentType.STATUS: self._handle_status,
            IntentType.LIST_PROJECTS: self._handle_list_projects,
            IntentType.SELECT_PROJECT: self._handle_select_project,
            IntentType.GIT_STATUS: self._handle_git_status,
            IntentType.GIT_DIFF: self._handle_git_diff,
            IntentType.GIT_LOG: self._handle_git_log,
            IntentType.RUN_COMMAND: self._handle_run_command,
            IntentType.AGENT_PROMPT: self._handle_agent_prompt,
            IntentType.DEPLOY: self._handle_deploy,
            IntentType.MCP_LIST: self._handle_mcp_list,
            IntentType.MCP_ADD: self._handle_mcp_add,
        }

        handler = handlers.get(intent.intent)
        if handler is None:
            return ActionResult(
                ActionResultType.ERROR,
                f"I didn't understand that. Try `help`.\n\nReceived: {intent.payload}",
            )

        try:
            if intent.intent == IntentType.AGENT_PROMPT:
                return await handler(
                    user_id,
                    intent,
                    agent_workspace,
                    project_id,
                    image_attachments=image_attachments,
                )
            return await handler(user_id, intent, workspace, project_id)
        except PermissionError as exc:
            return ActionResult(ActionResultType.ERROR, str(exc))
        except Exception as exc:
            return ActionResult(
                ActionResultType.ERROR,
                f"An error occurred: {exc}",
            )

    async def _handle_help(self, *_args: object) -> ActionResult:
        return ActionResult(ActionResultType.TEXT, HELP_TEXT)

    async def _handle_cancel(self, user_id: uuid.UUID, *_args: object) -> ActionResult:
        running = await self._tasks.list_running_for_user(user_id)
        cancelled = await self._runner.cancel_all()
        for task in running:
            await self._task_queue.publish_cancel(str(task.id))
            await self._tasks.mark_cancelled(task.id)
        total = cancelled + len(running)
        if total:
            return ActionResult(
                ActionResultType.TEXT,
                f"Cancelled {total} running process(es).",
            )
        return ActionResult(ActionResultType.TEXT, "No running tasks to cancel.")

    async def _handle_status(self, user_id: uuid.UUID, *_args: object) -> ActionResult:
        pending = await self._confirmations.get_pending_for_user(user_id)
        if pending:
            return ActionResult(
                ActionResultType.TEXT,
                f"Pending confirmation: {pending.action_type} (expires {pending.expires_at})",
            )
        return ActionResult(ActionResultType.TEXT, "No pending tasks or confirmations.")

    async def _handle_list_projects(
        self, user_id: uuid.UUID, *_args: object
    ) -> ActionResult:
        projects = await self._projects.sync_discovered(user_id)
        if not projects:
            discovered = self._projects.discover()
            if not discovered:
                return ActionResult(ActionResultType.TEXT, "No projects discovered.")
            lines = [f"• {p.name} ({p.root_path})" for p in discovered]
            return ActionResult(
                ActionResultType.TEXT,
                "Discovered projects (not yet synced):\n" + "\n".join(lines),
            )
        lines = [f"• {p.name} — {p.root_path}" for p in projects]
        return ActionResult(ActionResultType.TEXT, "Projects:\n" + "\n".join(lines))

    async def _handle_select_project(
        self, user_id: uuid.UUID, intent: ParsedIntent, *_args: object
    ) -> ActionResult:
        project = await self._projects.select_by_name(user_id, intent.project_name)
        if project is None:
            return ActionResult(
                ActionResultType.ERROR,
                f"Project '{intent.project_name}' not found.",
            )
        return ActionResult(
            ActionResultType.TEXT,
            f"Selected project: {project.name}\nPath: {project.root_path}",
        )

    async def _handle_git_status(
        self, _user_id: uuid.UUID, _intent: ParsedIntent, workspace: str, *_args: object
    ) -> ActionResult:
        result = await self._git.status(workspace)
        output = result.stdout or result.stderr
        return ActionResult(ActionResultType.TEXT, output or "(no output)")

    async def _handle_git_diff(
        self, _user_id: uuid.UUID, _intent: ParsedIntent, workspace: str, *_args: object
    ) -> ActionResult:
        result = await self._git.diff(workspace)
        output = result.stdout or result.stderr
        return ActionResult(ActionResultType.TEXT, output or "(no diff)")

    async def _handle_git_log(
        self, _user_id: uuid.UUID, _intent: ParsedIntent, workspace: str, *_args: object
    ) -> ActionResult:
        result = await self._git.log(workspace)
        output = result.stdout or result.stderr
        return ActionResult(ActionResultType.TEXT, output or "(no commits)")

    async def _handle_run_command(
        self,
        user_id: uuid.UUID,
        intent: ParsedIntent,
        workspace: str,
        project_id: uuid.UUID | None,
    ) -> ActionResult:
        classification = classify_command(intent.payload, self._settings)
        if classification.risk == CommandRisk.FORBIDDEN:
            return ActionResult(ActionResultType.ERROR, classification.reason)

        if requires_confirmation(classification):
            return await self._create_confirmation(
                user_id, "run_command", {"command": intent.payload, "workspace": workspace}
            )

        return await self._queue_command_task(
            user_id, intent.payload, workspace, project_id
        )

    async def queue_session_slash_command(
        self,
        user_id: uuid.UUID,
        command: str,
        workspace: str,
        project_id: uuid.UUID | None,
    ) -> ActionResult:
        """Run a built-in Cursor slash command against the active session."""
        agent_session = await self._sessions.get_active(user_id)
        if agent_session is None:
            return ActionResult(
                ActionResultType.ERROR,
                "Нет активной сессии. Отправь сообщение, /new или /resume.",
            )
        if not agent_session.cursor_chat_id:
            return ActionResult(
                ActionResultType.ERROR,
                "Чат Cursor ещё пуст. Сначала отправь хотя бы одно сообщение.",
            )

        return await self._queue_agent_work(
            user_id=user_id,
            prompt=command,
            workspace=workspace,
            project_id=project_id,
            session_id=agent_session.id,
            queued_message=f"Запускаю {command}…",
        )

    async def queue_deploy(
        self,
        user_id: uuid.UUID,
        *,
        workspace: str | None = None,
        project_id: uuid.UUID | None = None,
    ) -> ActionResult:
        if not self._settings.self_deploy_enabled:
            return ActionResult(
                ActionResultType.ERROR,
                "Self-deploy отключён. Установи SELF_DEPLOY_ENABLED=true.",
            )

        agent_session = await self._sessions.get_active(user_id)
        session_id = agent_session.id if agent_session else None
        deploy_workspace = workspace
        if deploy_workspace is None and agent_session is not None:
            deploy_workspace = agent_session.workspace_path
        if deploy_workspace is None and self._settings.agent_workspace is not None:
            deploy_workspace = str(self._settings.agent_workspace)

        task = await self._tasks.create(
            user_id=user_id,
            task_type="deploy",
            payload=json.dumps({"workspace": deploy_workspace} if deploy_workspace else {}),
            session_id=session_id,
            project_id=project_id,
        )
        await self._task_queue.enqueue(str(task.id))
        # The deploy script sends the single pre-restart warning; stay silent here.
        return ActionResult(
            ActionResultType.TASK_QUEUED,
            "",
            task_id=task.id,
            typing_indicator=False,
        )

    async def _handle_deploy(
        self, user_id: uuid.UUID, *_args: object
    ) -> ActionResult:
        return await self.queue_deploy(user_id)

    async def _handle_mcp_list(self, user_id: uuid.UUID, *_args: object) -> ActionResult:
        service = McpSetupService(
            self._db, self._settings, self._runner, self._task_queue
        )
        return ActionResult(ActionResultType.TEXT, await service.list_servers())

    async def _handle_mcp_add(
        self, user_id: uuid.UUID, intent: ParsedIntent, *_args: object
    ) -> ActionResult:
        if not intent.payload.strip():
            return ActionResult(
                ActionResultType.ERROR,
                "Укажи MCP: `добавь mcp github`",
            )
        service = McpSetupService(
            self._db, self._settings, self._runner, self._task_queue
        )
        try:
            message, confirmation_id = await service.start_add(user_id, intent.payload)
        except ValueError as exc:
            return ActionResult(ActionResultType.ERROR, str(exc))
        if confirmation_id is None:
            return ActionResult(ActionResultType.ERROR, message)
        return ActionResult(
            ActionResultType.MCP_SETUP,
            message,
            confirmation_id=confirmation_id,
        )

    async def _handle_agent_prompt(
        self,
        user_id: uuid.UUID,
        intent: ParsedIntent,
        workspace: str,
        project_id: uuid.UUID | None,
        image_attachments: list[StoredImage] | None = None,
    ) -> ActionResult:
        prompt = intent.payload
        if image_attachments:
            prompt = build_prompt_with_images(
                prompt, image_attachments, settings=self._settings
            )
        agent_session = await self._sessions.get_or_create_active(
            user_id, workspace, project_id
        )
        return await self._queue_agent_work(
            user_id=user_id,
            prompt=prompt,
            workspace=workspace,
            project_id=project_id,
            session_id=agent_session.id,
        )

    async def _queue_agent_work(
        self,
        *,
        user_id: uuid.UUID,
        prompt: str,
        workspace: str,
        project_id: uuid.UUID | None,
        session_id: uuid.UUID,
        queued_message: str = "Передала Cursor. Он ответит здесь, как закончит.",
    ) -> ActionResult:
        payload_dict = {
            "prompt": prompt,
            "workspace": workspace,
            "agent_workspace": self._settings.normalize_cursor_workspace(
                workspace
            ),
        }
        payload_json = json.dumps(payload_dict, ensure_ascii=False)

        session_exec = (
            SessionExecutionService(self._redis)
            if self._redis is not None
            else None
        )

        running_task_id = await self._live_running_task_id(session_id, session_exec)

        if running_task_id is not None:
            if session_exec is None:
                return ActionResult(
                    ActionResultType.ERROR,
                    "Задача уже выполняется. Дождись ответа или отправь cancel.",
                )
            await session_exec.set_pending_redirect(session_id, payload_dict)
            await session_exec.set_state(session_id, SessionExecState.INTERRUPTING)
            await session_exec.publish_redirect(session_id)
            await self._task_queue.publish_cancel(str(running_task_id))
            logger.info(
                "redirect_requested",
                session_id=str(session_id),
                task_id=str(running_task_id),
                user_id=str(user_id),
            )
            return ActionResult(
                ActionResultType.REDIRECT_REQUESTED,
                "",
                task_id=running_task_id,
            )

        pending = await self._tasks.list_pending_agent_for_session(session_id)
        if pending:
            primary = pending[0]
            for duplicate in pending[1:]:
                await self._tasks.mark_cancelled(duplicate.id)
                logger.info(
                    "redirect_coalesced_duplicate_pending",
                    session_id=str(session_id),
                    cancelled_task_id=str(duplicate.id),
                )
            await self._tasks.update_payload(primary.id, payload_json)
            logger.info(
                "redirect_coalesced_pending",
                session_id=str(session_id),
                task_id=str(primary.id),
            )
            return ActionResult(
                ActionResultType.TASK_QUEUED,
                "",
                task_id=primary.id,
            )

        task = await self._tasks.create(
            user_id=user_id,
            task_type="agent_prompt",
            payload=payload_json,
            session_id=session_id,
            project_id=project_id,
        )
        await self._task_queue.enqueue(str(task.id))
        logger.info(
            "task_started",
            session_id=str(session_id),
            task_id=str(task.id),
            user_id=str(user_id),
            source="enqueue",
        )
        return ActionResult(
            ActionResultType.TASK_QUEUED,
            queued_message,
            task_id=task.id,
        )

    async def _live_running_task_id(
        self,
        session_id: uuid.UUID,
        session_exec: SessionExecutionService | None,
    ) -> uuid.UUID | None:
        """Redirect only into a worker that is still alive.

        A DB row or Redis key left by SIGTERM is not a running turn. Treating
        it as one publishes a redirect that nobody consumes, and every later
        message follows the same dead path.
        """
        running_tasks = await self._tasks.list_running_agent_for_session(session_id)
        if session_exec is None:
            return running_tasks[0].id if running_tasks else None

        live_task_id = await session_exec.get_running_task_id(session_id)
        if live_task_id is not None:
            return live_task_id

        for task in running_tasks:
            await self._tasks.mark_failed(
                task.id,
                "Worker that owned this turn is gone.",
            )
            logger.info(
                "stale_running_turn_cleared",
                session_id=str(session_id),
                task_id=str(task.id),
            )
        return None

    async def _create_confirmation(
        self,
        user_id: uuid.UUID,
        action_type: str,
        payload: dict[str, str],
    ) -> ActionResult:
        confirmation = await self._confirmations.create(
            user_id=user_id,
            action_type=action_type,
            action_payload=json.dumps(payload),
            ttl_seconds=self._settings.confirmation_ttl_seconds,
        )
        return ActionResult(
            ActionResultType.CONFIRMATION_REQUIRED,
            f"Confirmation required for: {payload.get('command', action_type)}",
            confirmation_id=confirmation.id,
        )

    async def _queue_command_task(
        self,
        user_id: uuid.UUID,
        command: str,
        workspace: str,
        project_id: uuid.UUID | None,
    ) -> ActionResult:
        task = await self._tasks.create(
            user_id=user_id,
            task_type="run_command",
            payload=json.dumps({"command": command, "workspace": workspace}),
            project_id=project_id,
        )
        await self._task_queue.enqueue(str(task.id))
        return ActionResult(
            ActionResultType.TASK_QUEUED,
            f"Command queued: `{command}`",
            task_id=task.id,
        )

    async def execute_confirmed_action(
        self, user_id: uuid.UUID, confirmation_id: uuid.UUID
    ) -> ActionResult:
        confirmation = await self._confirmations.approve(confirmation_id)
        if confirmation is None:
            return ActionResult(ActionResultType.ERROR, "Confirmation expired or invalid.")

        if confirmation.user_id != user_id:
            return ActionResult(ActionResultType.ERROR, "Not your confirmation.")

        payload = json.loads(confirmation.action_payload)
        if confirmation.action_type == "run_command":
            task = await self._tasks.create(
                user_id=user_id,
                task_type="run_command",
                payload=confirmation.action_payload,
            )
            await self._task_queue.enqueue(str(task.id))
            return ActionResult(
                ActionResultType.TASK_QUEUED,
                f"Confirmed. Running: `{payload.get('command', '')}`",
                task_id=task.id,
            )

        return ActionResult(ActionResultType.ERROR, "Unknown action type.")
