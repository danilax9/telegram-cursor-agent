"""Typed deterministic service actions with safe fallback."""

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

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


class ActionResultType(StrEnum):
    TEXT = "text"
    CONFIRMATION_REQUIRED = "confirmation_required"
    TASK_QUEUED = "task_queued"
    ERROR = "error"


@dataclass
class ActionResult:
    result_type: ActionResultType
    message: str
    task_id: uuid.UUID | None = None
    confirmation_id: uuid.UUID | None = None


ActionHandler = Callable[..., Awaitable[ActionResult]]


class ActionService:
    """Routes parsed intents to typed handlers."""

    def __init__(
        self,
        db: AsyncSession,
        settings: Settings,
        runner: ProcessRunner,
        task_queue: TaskQueue,
    ) -> None:
        self._db = db
        self._settings = settings
        self._runner = runner
        self._task_queue = task_queue
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
    ) -> ActionResult:
        intent = parse_intent(text)
        return await self._dispatch(user_id, intent, workspace, project_id)

    async def _dispatch(
        self,
        user_id: uuid.UUID,
        intent: ParsedIntent,
        workspace: str,
        project_id: uuid.UUID | None,
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
        }

        handler = handlers.get(intent.intent)
        if handler is None:
            return ActionResult(
                ActionResultType.ERROR,
                f"I didn't understand that. Try `help`.\n\nReceived: {intent.payload}",
            )

        try:
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

    async def _handle_agent_prompt(
        self,
        user_id: uuid.UUID,
        intent: ParsedIntent,
        workspace: str,
        project_id: uuid.UUID | None,
    ) -> ActionResult:
        agent_session = await self._sessions.get_or_create_active(
            user_id, workspace, project_id
        )
        task = await self._tasks.create(
            user_id=user_id,
            task_type="agent_prompt",
            payload=json.dumps({"prompt": intent.payload, "workspace": workspace}),
            session_id=agent_session.id,
            project_id=project_id,
        )
        await self._task_queue.enqueue(str(task.id))
        return ActionResult(
            ActionResultType.TASK_QUEUED,
            "Передала Cursor. Он ответит здесь, как закончит.",
            task_id=task.id,
        )

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
