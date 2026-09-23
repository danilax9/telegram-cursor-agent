"""Recover Telegram/Cursor sessions after self-deploy restarts."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import uuid
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import async_sessionmaker

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.logging import get_logger
from telegram_cursor_agent.database.repositories.session import SessionRepository
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.deploy_context_snapshot import (
    refresh_deploy_context_from_db,
)
from telegram_cursor_agent.services.deploy_resume import (
    DEPLOY_RESUME_PROMPT,
    DEPLOY_SUCCESS_MESSAGE,
    INTERRUPTED_RECOVERY_MESSAGE,
    MAX_RECOVERY_ATTEMPTS,
    NO_SESSION_RECOVERY_MESSAGE,
    RECOVERY_ATTEMPT_KEY,
    RECOVERY_EXHAUSTED_MESSAGE,
    SESSION_ACTIVE_STATUS,
    DeployContext,
    build_interrupt_recovery_prompt,
    claim_marker_for_delivery,
    clear_marker,
    consume_clean_shutdown,
    read_marker,
    release_claim,
    should_recover_deploy,
    store_recovery_state,
)
from telegram_cursor_agent.services.session_execution import SessionExecutionService
from telegram_cursor_agent.telegram.notifier import TelegramNotifier

logger = get_logger(__name__)


class DeployRecoveryService:
    def __init__(
        self,
        settings: Settings,
        session_factory: async_sessionmaker,
        notifier: TelegramNotifier | None = None,
        redis: Redis | None = None,
    ) -> None:
        self._settings = settings
        self._session_factory = session_factory
        self._notifier = notifier
        self._redis = redis

    async def recover_tasks_on_startup(self) -> None:
        notifications: list[dict[str, object]] = []
        resumed_sessions: set[uuid.UUID] = set()
        interrupt_resumes: list[tuple[DeployContext, int, str]] = []
        # systemctl / self-deploy sends SIGTERM. That is a normal restart, not
        # a crash loop, so it must not exhaust the auto-resume budget.
        intentional_stop = consume_clean_shutdown(self._settings)
        marker = read_marker(self._settings)
        deploy_recovery = marker is not None and should_recover_deploy(marker)
        if deploy_recovery:
            context = marker.to_context()
            message = await self._finalize_deploy_marker(marker, context)
            if message and context.telegram_id is not None:
                notifications.append(
                    {"telegram_id": context.telegram_id, "message": message}
                )
            if context.session_id is not None:
                resumed_sessions.add(context.session_id)

        async with self._session_factory() as db:
            tasks = TaskRepository(db)
            users = UserRepository(db)
            sessions = SessionRepository(db)
            # No limit: every interrupted task must leave the "running" state,
            # otherwise it stays stuck forever and blocks its session.
            interrupted = await tasks.list_running(limit=None)

            for task in interrupted:
                await self._release_session_lease(task.session_id)
                user = await users.get_by_id(task.user_id)
                self._terminate_orphan_process(task.process_pid)

                if task.task_type == "deploy":
                    await tasks.mark_completed(task.id, DEPLOY_SUCCESS_MESSAGE)
                    if user is not None:
                        notifications.append(
                            {
                                "telegram_id": user.telegram_id,
                                "message": DEPLOY_SUCCESS_MESSAGE,
                            }
                        )
                    continue

                if task.task_type != "agent_prompt":
                    await tasks.mark_failed(
                        task.id,
                        "Interrupted by service restart.",
                    )
                    continue

                if task.session_id in resumed_sessions:
                    await tasks.mark_failed(
                        task.id,
                        "Superseded by deploy recovery resume.",
                    )
                    continue

                cursor_chat_id = None
                workspace = None
                if task.session_id:
                    agent_session = await sessions.get_by_id(task.session_id)
                    if agent_session:
                        cursor_chat_id = agent_session.cursor_chat_id
                        workspace = agent_session.workspace_path

                payload = {}
                if task.payload:
                    try:
                        payload = json.loads(task.payload)
                    except json.JSONDecodeError:
                        payload = {}
                workspace = workspace or payload.get("agent_workspace") or payload.get(
                    "workspace"
                )
                if workspace:
                    workspace = self._settings.normalize_cursor_workspace(str(workspace))
                attempt = _recovery_attempt(payload)

                if not cursor_chat_id or not workspace:
                    await tasks.mark_failed(
                        task.id,
                        "Interrupted by service restart (no session to resume).",
                    )
                    if user is not None:
                        notifications.append(
                            {
                                "telegram_id": user.telegram_id,
                                "message": NO_SESSION_RECOVERY_MESSAGE,
                            }
                        )
                    continue

                if not intentional_stop and attempt >= MAX_RECOVERY_ATTEMPTS:
                    await tasks.mark_failed(
                        task.id,
                        "Interrupted by service restart (recovery attempts exhausted).",
                    )
                    if user is not None:
                        notifications.append(
                            {
                                "telegram_id": user.telegram_id,
                                "message": RECOVERY_EXHAUSTED_MESSAGE,
                            }
                        )
                    logger.warning(
                        "recovery_attempts_exhausted",
                        task_id=str(task.id),
                        attempt=attempt,
                    )
                    if task.session_id:
                        resumed_sessions.add(task.session_id)
                    continue

                await tasks.mark_failed(
                    task.id,
                    "Interrupted by service restart.",
                )
                if user is not None:
                    notifications.append(
                        {
                            "telegram_id": user.telegram_id,
                            "message": INTERRUPTED_RECOVERY_MESSAGE,
                        }
                    )
                    if not deploy_recovery:
                        interrupt_resumes.append(
                            (
                                DeployContext(
                                    user_id=task.user_id,
                                    telegram_id=user.telegram_id,
                                    session_id=task.session_id,
                                    cursor_chat_id=str(cursor_chat_id),
                                    workspace=str(workspace),
                                ),
                                1 if intentional_stop else attempt + 1,
                                str(payload.get("prompt", "")),
                            )
                        )
                if task.session_id:
                    resumed_sessions.add(task.session_id)

            await db.commit()

        if deploy_recovery and marker is not None:
            deduped = _dedupe_notifications(notifications)
            if marker.telegram_id is not None:
                deduped = _ensure_online_notification(
                    deduped, int(marker.telegram_id)
                )
            store_recovery_state(self._settings, notifications=deduped)
            await self._queue_session_resume(
                await refresh_deploy_context_from_db(
                    self._session_factory, marker.to_context(), self._settings
                ),
                prompt=DEPLOY_RESUME_PROMPT,
                recovery_flag="deploy_recovery",
                attempt=1,
                silent=True,
            )
            updated_marker = read_marker(self._settings)
            recovery_token = (
                updated_marker.recovery_token if updated_marker is not None else None
            )
            if self._redis is not None and recovery_token:
                await self._redis.set(
                    self._settings.deploy_worker_ready_key,
                    recovery_token,
                    ex=600,
                )
            logger.info("deploy_tasks_recovered", notifications=len(deduped))
            return

        # Unexpected restart (manual restart, crash, env reload, OOM kill...).
        # There is no deploy marker to hand over to the bot, so the worker
        # notifies directly instead of staying silent.
        deduped = _dedupe_notifications(notifications)
        if deduped:
            delivered = await self._send_notifications(deduped)
            logger.info("interrupt_notifications_delivered", count=delivered)

        for context, attempt, original_prompt in interrupt_resumes:
            refreshed = await refresh_deploy_context_from_db(
                self._session_factory, context, self._settings
            )
            await self._queue_session_resume(
                refreshed,
                prompt=build_interrupt_recovery_prompt(original_prompt),
                recovery_flag="interrupt_recovery",
                attempt=attempt,
            )

        if marker is not None and marker.status == SESSION_ACTIVE_STATUS:
            stale_context = await refresh_deploy_context_from_db(
                self._session_factory, marker.to_context(), self._settings
            )
            if (
                stale_context.cursor_chat_id
                and stale_context.workspace
                and stale_context.user_id is not None
                and stale_context.session_id not in resumed_sessions
            ):
                await self._queue_session_resume(
                    stale_context,
                    prompt=build_interrupt_recovery_prompt(None),
                    recovery_flag="interrupt_recovery",
                    attempt=1,
                    silent=True,
                )
            clear_marker(self._settings)
            logger.info("stale_session_active_marker_cleared")

    async def deliver_stored_notifications(self) -> None:
        """Send recovery messages from the worker (does not require bot restart)."""
        if self._notifier is None:
            return
        marker = read_marker(self._settings)
        if marker is None or not marker.tasks_recovered:
            return
        claimed = claim_marker_for_delivery(self._settings)
        if claimed is None:
            return
        delivered = 0
        try:
            notifications = list(claimed.notifications or [])
            if claimed.telegram_id is not None:
                notifications = _ensure_online_notification(
                    notifications, int(claimed.telegram_id)
                )
            delivered = await self._send_notifications(
                _dedupe_notifications(notifications)
            )
        finally:
            release_claim(self._settings)
        logger.info("deploy_notifications_delivered_by_worker", count=delivered)

    async def deliver_notifications_when_ready(self) -> None:
        if self._notifier is None or self._redis is None:
            return

        await asyncio.sleep(self._settings.deploy_recovery_delay_seconds)

        early = read_marker(self._settings)
        if early is not None and not early.tasks_recovered and not should_recover_deploy(
            early
        ):
            # Stale session_active marker from an unexpected restart: there is no
            # deploy handover coming, so waiting for one would log a false timeout.
            return

        marker = await self._wait_for_marker_recovery()
        if marker is None or not marker.tasks_recovered:
            return

        if not await self._wait_for_worker_ready(marker.recovery_token):
            logger.warning("deploy_worker_ready_timeout")
            return

        claimed = claim_marker_for_delivery(self._settings)
        if claimed is None:
            # Worker already delivered these notifications.
            return
        delivered = 0
        try:
            notifications = list(claimed.notifications or [])
            if claimed.telegram_id is not None:
                notifications = _ensure_online_notification(
                    notifications, int(claimed.telegram_id)
                )
            delivered = await self._send_notifications(
                _dedupe_notifications(notifications)
            )
        finally:
            release_claim(self._settings)
        logger.info("deploy_notifications_delivered", count=delivered)

    async def _send_notifications(self, notifications: list[dict[str, object]]) -> int:
        """Deliver each message independently so one failure cannot silence the rest."""
        if self._notifier is None:
            return 0
        delivered = 0
        for item in notifications:
            telegram_id = item.get("telegram_id")
            message = item.get("message")
            if telegram_id is None or not message:
                continue
            try:
                await self._notifier.send(int(telegram_id), str(message))
                delivered += 1
            except Exception:
                logger.exception(
                    "recovery_notification_failed",
                    telegram_id=telegram_id,
                )
        return delivered

    def _terminate_orphan_process(self, pid: int | None) -> None:
        """Kill a cursor-agent process that outlived its worker (crash, SIGKILL)."""
        if not pid or pid <= 1:
            return
        try:
            cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="ignore")
        except OSError:
            return
        # Guard against PID reuse: only kill something that still looks like ours.
        if "cursor" not in cmdline and "agent" not in cmdline:
            return
        try:
            os.kill(pid, signal.SIGKILL)
            logger.info("orphan_process_killed", pid=pid)
        except (OSError, ProcessLookupError):
            return

    async def _wait_for_marker_recovery(self):
        for _ in range(60):
            marker = read_marker(self._settings)
            if marker is not None and marker.tasks_recovered:
                return marker
            await asyncio.sleep(0.5)
        marker = read_marker(self._settings)
        if marker is not None and marker.tasks_recovered:
            return marker
        return None

    async def _wait_for_worker_ready(self, recovery_token: str | None) -> bool:
        if self._redis is None:
            return recovery_token is None
        key = self._settings.deploy_worker_ready_key
        if recovery_token:
            for _ in range(60):
                value = await self._redis.get(key)
                if value is not None and value.decode() == recovery_token:
                    await asyncio.sleep(1)
                    return True
                await asyncio.sleep(0.5)
            return False

        for _ in range(60):
            if await self._redis.get(key):
                await asyncio.sleep(1)
                return True
            await asyncio.sleep(0.5)
        return False

    async def _queue_session_resume(
        self,
        context: DeployContext,
        *,
        prompt: str,
        recovery_flag: str,
        attempt: int,
        silent: bool = False,
    ) -> None:
        context = await refresh_deploy_context_from_db(
            self._session_factory, context, self._settings
        )
        if (
            self._redis is None
            or context.user_id is None
            or context.cursor_chat_id is None
            or not context.workspace
        ):
            logger.warning(
                "session_resume_skipped_missing_context",
                session_id=str(context.session_id),
                has_chat_id=context.cursor_chat_id is not None,
                has_workspace=bool(context.workspace),
            )
            return

        payload_body: dict[str, object] = {
            "prompt": prompt,
            "agent_workspace": context.workspace,
            recovery_flag: True,
            RECOVERY_ATTEMPT_KEY: attempt,
            "cursor_chat_id": context.cursor_chat_id,
        }
        if silent:
            payload_body["silent_recovery"] = True

        async with self._session_factory() as db:
            tasks = TaskRepository(db)
            task = await tasks.create(
                user_id=context.user_id,
                task_type="agent_prompt",
                session_id=context.session_id,
                payload=json.dumps(payload_body),
            )
            await db.commit()

        queue = TaskQueue(self._redis)
        await queue.enqueue(str(task.id))
        logger.info(
            "session_resume_task_queued",
            task_id=str(task.id),
            recovery_flag=recovery_flag,
            attempt=attempt,
        )

    async def _release_session_lease(self, session_id: uuid.UUID | None) -> None:
        """A restarted worker does not own turns whose process already died."""
        if session_id is None or self._redis is None:
            return
        try:
            await SessionExecutionService(self._redis).abandon_session(session_id)
        except Exception:
            logger.exception(
                "session_lease_release_failed",
                session_id=str(session_id),
            )

    async def _finalize_deploy_marker(self, marker, context: DeployContext) -> str | None:
        if context.telegram_id is None:
            return None
        if context.task_id is not None:
            async with self._session_factory() as db:
                tasks = TaskRepository(db)
                task = await tasks.get_by_id(context.task_id)
                if task is not None and task.status == "completed":
                    return None
                if task is not None and task.status == "running":
                    await tasks.mark_completed(context.task_id, DEPLOY_SUCCESS_MESSAGE)
                    await db.commit()
                    return DEPLOY_SUCCESS_MESSAGE
        return DEPLOY_SUCCESS_MESSAGE


def _recovery_attempt(payload: dict) -> int:
    try:
        return int(payload.get(RECOVERY_ATTEMPT_KEY, 0))
    except (TypeError, ValueError):
        return 0


def _ensure_online_notification(
    notifications: list[dict[str, object]],
    telegram_id: int,
) -> list[dict[str, object]]:
    """Queue exactly one post-deploy online message (delivery is after worker restart)."""
    rest = [
        item
        for item in notifications
        if int(item.get("telegram_id", -1)) != telegram_id
        or item.get("message") != DEPLOY_SUCCESS_MESSAGE
    ]
    return [
        {"telegram_id": telegram_id, "message": DEPLOY_SUCCESS_MESSAGE},
        *rest,
    ]


def _dedupe_notifications(
    notifications: list[dict[str, object]],
) -> list[dict[str, object]]:
    seen: set[int] = set()
    deduped: list[dict[str, object]] = []
    for item in notifications:
        telegram_id = item.get("telegram_id")
        if telegram_id is None:
            continue
        tid = int(telegram_id)
        if tid in seen:
            continue
        seen.add(tid)
        deduped.append(item)
    return deduped
