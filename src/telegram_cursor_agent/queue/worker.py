"""Background task worker."""

import asyncio
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from telegram_cursor_agent.agent.adapter import AgentResult, CursorAgentAdapter
from telegram_cursor_agent.core.config import get_settings
from telegram_cursor_agent.core.logging import get_logger, setup_logging
from telegram_cursor_agent.database.models.task import Task
from telegram_cursor_agent.database.repositories.session import SessionRepository
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.database.session import create_engine, create_session_factory
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.queue.task_queue import TaskQueue, create_redis
from telegram_cursor_agent.services.agent_errors import (
    AgentRunOutcome,
    AgentTimeoutError,
    classify_agent_result,
    is_account_switchable_failure,
)
from telegram_cursor_agent.services.cursor_account_login import (
    CURSOR_LOGIN_TASK_NOTIFIED,
    CursorAccountLoginService,
)
from telegram_cursor_agent.services.cursor_accounts import CursorAccountService
from telegram_cursor_agent.services.cursor_models import (
    parse_models_output,
    write_models_catalog,
)
from telegram_cursor_agent.services.deploy import DeployService
from telegram_cursor_agent.services.deploy_recovery import DeployRecoveryService
from telegram_cursor_agent.services.deploy_resume import (
    DEPLOY_START_WARNING,
    DEPLOY_SUCCESS_MESSAGE,
    SESSION_ACTIVE_STATUS,
    DeployContext,
    claim_deploy_start_notification,
    clear_marker,
    read_marker,
    update_marker_completed,
    write_marker,
)
from telegram_cursor_agent.services.mcp_setup import McpSetupService
from telegram_cursor_agent.telegram.live_message import LiveMessageNotifier
from telegram_cursor_agent.telegram.notifier import TelegramNotifier

logger = get_logger(__name__)

WATCHDOG_INTERVAL_SECONDS = 60
WATCHDOG_GRACE_SECONDS = 300
STATUS_PERSIST_ATTEMPTS = 3
MAX_LOOP_BACKOFF_SECONDS = 30

STUCK_TASK_MESSAGE = (
    "⚠️ Задача зависла и была снята по таймауту. "
    "Отправь сообщение снова — сессия сохранена."
)


class TaskWorker:
    def __init__(self) -> None:
        self._settings = get_settings()
        self._engine = create_engine(self._settings)
        self._session_factory = create_session_factory(self._engine)
        self._runner = ProcessRunner(self._settings)
        self._redis: Redis | None = None  # type: ignore[type-arg]
        self._queue: TaskQueue | None = None
        self._notifier = TelegramNotifier(self._settings)
        self._active_task_pids: dict[str, int] = {}
        self._current_task_id: str | None = None
        self._last_watchdog_at = 0.0

    async def start(self) -> None:
        setup_logging(self._settings)
        self._redis = await create_redis(self._settings)
        self._queue = TaskQueue(self._redis)
        recovery = DeployRecoveryService(
            self._settings,
            self._session_factory,
            notifier=self._notifier,
            redis=self._redis,
        )
        try:
            await recovery.recover_tasks_on_startup()
            await recovery.deliver_stored_notifications()
        except Exception:
            # A recovery failure must never stop the worker from serving new tasks.
            logger.exception("startup_recovery_failed")
        logger.info("worker_started")
        cancel_listener = asyncio.create_task(self._supervise_cancellations())

        failures = 0
        try:
            while True:
                try:
                    if self._queue is None:
                        await asyncio.sleep(1)
                        continue
                    task_id = await self._queue.dequeue(block_seconds=5)
                    if task_id:
                        await self._process_task(task_id)
                    else:
                        await self._recover_pending_tasks()
                        await self._watchdog_stuck_tasks()
                    failures = 0
                    if await self._should_restart_after_deploy():
                        break
                except Exception:
                    failures += 1
                    logger.exception("worker_loop_error", consecutive_failures=failures)
                    await asyncio.sleep(
                        min(2**failures, MAX_LOOP_BACKOFF_SECONDS)
                    )
        finally:
            cancel_listener.cancel()
            await asyncio.gather(cancel_listener, return_exceptions=True)
            await self._shutdown()
        logger.info("worker_restarting_after_deploy")

    async def _shutdown(self) -> None:
        for closer in (self._notifier.close, self._close_redis):
            try:
                await closer()
            except Exception:
                logger.warning("worker_shutdown_cleanup_failed", exc_info=True)

    async def _close_redis(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()  # type: ignore[attr-defined]

    async def _should_restart_after_deploy(self) -> bool:
        if self._redis is None:
            return False
        key = self._settings.deploy_worker_restart_key
        pending = await self._redis.get(key)
        if pending is None:
            return False
        await self._redis.delete(key)
        await self._prepare_graceful_restart()
        return True

    async def _prepare_graceful_restart(self) -> None:
        marker = read_marker(self._settings)
        if marker is None or marker.telegram_id is None:
            return
        telegram_id = claim_deploy_start_notification(self._settings)
        if telegram_id is not None:
            try:
                await self._notifier.send(telegram_id, DEPLOY_START_WARNING)
            except Exception:
                logger.exception("deploy_start_warning_failed")
        update_marker_completed(self._settings)

    async def _supervise_cancellations(self) -> None:
        """Keep the listener alive: a dropped Redis pubsub must not disable /cancel."""
        while True:
            try:
                await self._listen_cancellations()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("cancel_listener_error")
            await asyncio.sleep(1)

    async def _listen_cancellations(self) -> None:
        if self._queue is None:
            return
        async for task_id in self._queue.listen_cancel():
            pid = self._active_task_pids.get(task_id)
            if pid is not None:
                await self._runner.cancel_pid(pid)
                continue
            async with self._session_factory() as db:
                task = await TaskRepository(db).get_by_id(uuid.UUID(task_id))
                if task and task.process_pid:
                    await self._runner.cancel_pid(task.process_pid)

    async def _recover_pending_tasks(self) -> None:
        async with self._session_factory() as db:
            pending = await TaskRepository(db).list_pending(limit=10)
        for task in pending:
            await self._process_task(str(task.id))

    async def _process_task(self, task_id_str: str) -> None:
        task_id = uuid.UUID(task_id_str)
        claimed = await self._claim_task(task_id)
        if claimed is None:
            return
        task, telegram_id = claimed
        self._current_task_id = task_id_str
        try:
            await self._run_claimed_task(task, telegram_id)
        except Exception as exc:
            # Safety net: whatever fails, the task never stays "running" in silence.
            logger.exception("task_failed", task_id=task_id_str)
            await self._fail_task(task_id, telegram_id, exc)
        finally:
            self._current_task_id = None
            self._active_task_pids.pop(task_id_str, None)

    async def _claim_task(self, task_id: uuid.UUID) -> tuple[Task, int | None] | None:
        """Mark the task running in a short transaction and snapshot what we need."""
        async with self._session_factory() as db:
            tasks = TaskRepository(db)
            task = await tasks.get_by_id(task_id)
            if task is None or task.status != "pending":
                return None
            await tasks.mark_running(task_id)
            user = await UserRepository(db).get_by_id(task.user_id)
            telegram_id = user.telegram_id if user is not None else None
            await db.commit()
        return task, telegram_id

    async def _run_claimed_task(self, task: Task, telegram_id: int | None) -> None:
        task_id = task.id
        task_id_str = str(task_id)
        payload = json.loads(task.payload or "{}")
        silent_recovery = bool(payload.get("silent_recovery"))
        is_system_recovery = bool(
            payload.get("deploy_recovery") or payload.get("interrupt_recovery")
        )
        skip_typing = task.task_type in {
            "cursor_account_login",
            "cursor_account_login_cancel",
        }
        typing_task = (
            asyncio.create_task(self._notifier.keep_typing(telegram_id))
            if telegram_id is not None and not skip_typing
            else None
        )

        async def stop_typing() -> None:
            nonlocal typing_task
            if typing_task is None:
                return
            typing_task.cancel()
            await asyncio.gather(typing_task, return_exceptions=True)
            typing_task = None

        try:
            if (
                telegram_id is not None
                and task.task_type == "agent_prompt"
                and not is_system_recovery
            ):
                write_marker(
                    self._settings,
                    await self._build_deploy_context(task),
                    status=SESSION_ACTIVE_STATUS,
                )

            live: LiveMessageNotifier | None = None
            if (
                telegram_id is not None
                and task.task_type == "agent_prompt"
                and not silent_recovery
            ):
                live = LiveMessageNotifier(self._notifier, self._settings, telegram_id)

            async def on_progress(text: str) -> None:
                if live is not None:
                    await live.update(text)

            try:
                result = await self._execute(task, on_progress if live is not None else None)
            except AgentTimeoutError as exc:
                timeout_error = str(exc)
                timeout_message = self._timeout_message(exc)
                await stop_typing()
                self._clear_session_marker(task, is_system_recovery)
                logger.warning(
                    "task_timed_out", task_id=task_id_str, seconds=exc.seconds
                )
                await self._persist_status(
                    task_id, lambda repo: repo.mark_failed(task_id, timeout_error)
                )
                if telegram_id is not None:
                    await self._deliver_result(live, telegram_id, timeout_message)
                return
            await stop_typing()

            self._clear_session_marker(task, is_system_recovery)

            if result == "Task cancelled.":
                await self._persist_status(task_id, lambda repo: repo.mark_cancelled(task_id))
                await self._notify_safe(telegram_id, "Задача отменена.")
                return

            user_message = DEPLOY_SUCCESS_MESSAGE if task.task_type == "deploy" else result
            # Persist before delivering: a Telegram outage must not discard the result.
            await self._persist_status(
                task_id, lambda repo: repo.mark_completed(task_id, user_message)
            )
            if (
                telegram_id is not None
                and not silent_recovery
                and task.task_type != "cursor_account_login_cancel"
                and result != CURSOR_LOGIN_TASK_NOTIFIED
            ):
                await self._deliver_result(live, telegram_id, user_message)
        finally:
            await stop_typing()

    def _clear_session_marker(self, task: Task, is_system_recovery: bool) -> None:
        if task.task_type != "agent_prompt" or is_system_recovery:
            return
        marker = read_marker(self._settings)
        if (
            marker is not None
            and marker.status == SESSION_ACTIVE_STATUS
            and marker.task_id == str(task.id)
        ):
            clear_marker(self._settings)

    def _timeout_message(self, exc: AgentTimeoutError) -> str:
        minutes = max(1, exc.seconds // 60)
        text = (
            f"⏱ Остановил задачу по лимиту времени ({minutes} мин). "
            "Сессия сохранена — отправь /resume или новое сообщение, чтобы продолжить."
        )
        partial = exc.partial_output.strip()
        if partial:
            text = f"{text}\n\nЧто успел сделать:\n\n{partial}"
        return text

    async def _persist_status(
        self,
        task_id: uuid.UUID,
        apply: Callable[[TaskRepository], Awaitable[object]],
    ) -> bool:
        """Write a terminal status, retrying so a DB blip cannot orphan the task."""
        for attempt in range(1, STATUS_PERSIST_ATTEMPTS + 1):
            try:
                async with self._session_factory() as db:
                    await apply(TaskRepository(db))
                    await db.commit()
                return True
            except Exception:
                if attempt == STATUS_PERSIST_ATTEMPTS:
                    logger.exception("task_status_persist_failed", task_id=str(task_id))
                    return False
                logger.warning(
                    "task_status_persist_retry", task_id=str(task_id), attempt=attempt
                )
                await asyncio.sleep(attempt)
        return False

    async def _fail_task(
        self, task_id: uuid.UUID, telegram_id: int | None, exc: BaseException
    ) -> None:
        await self._persist_status(task_id, lambda repo: repo.mark_failed(task_id, str(exc)))
        await self._notify_safe(telegram_id, f"Cursor завершился с ошибкой:\n{exc}")

    async def _notify_safe(self, telegram_id: int | None, text: str) -> None:
        if telegram_id is None:
            return
        try:
            await self._notifier.send(telegram_id, text)
        except Exception:
            logger.exception("notify_failed", telegram_id=telegram_id)

    async def _deliver_result(
        self, live: LiveMessageNotifier | None, telegram_id: int, text: str
    ) -> None:
        if live is not None and live.message_id is not None:
            try:
                await live.finalize(text)
                return
            except Exception:
                # Editing can fail (message deleted, too long) — fall back to a new message.
                logger.warning("live_finalize_failed", telegram_id=telegram_id)
        await self._notify_safe(telegram_id, text)

    async def _watchdog_stuck_tasks(self) -> None:
        """Fail tasks abandoned by a worker that died before recovery could run."""
        now = time.monotonic()
        if now - self._last_watchdog_at < WATCHDOG_INTERVAL_SECONDS:
            return
        self._last_watchdog_at = now
        cutoff = datetime.now(UTC) - timedelta(
            seconds=self._settings.task_timeout * 2 + WATCHDOG_GRACE_SECONDS
        )
        async with self._session_factory() as db:
            running = await TaskRepository(db).list_running(limit=None)
        for task in running:
            if str(task.id) == self._current_task_id:
                continue
            started = task.started_at or task.created_at
            if started is None:
                continue
            if started.tzinfo is None:
                started = started.replace(tzinfo=UTC)
            if started > cutoff:
                continue
            telegram_id = await self._lookup_telegram_id(task.user_id)
            logger.warning(
                "stuck_task_reclaimed", task_id=str(task.id), started_at=str(started)
            )
            await self._mark_stuck(task.id)
            marker = read_marker(self._settings)
            if marker is not None and marker.task_id == str(task.id):
                clear_marker(self._settings)
            await self._notify_safe(telegram_id, STUCK_TASK_MESSAGE)

    async def _mark_stuck(self, task_id: uuid.UUID) -> None:
        async def apply(repo: TaskRepository) -> None:
            await repo.mark_failed(task_id, "stuck task timeout")

        await self._persist_status(task_id, apply)

    async def _lookup_telegram_id(self, user_id: uuid.UUID) -> int | None:
        async with self._session_factory() as db:
            user = await UserRepository(db).get_by_id(user_id)
        return user.telegram_id if user is not None else None

    async def _execute(
        self, task: Task, on_progress: Callable[[str], Awaitable[None]] | None = None
    ) -> str:
        payload = json.loads(task.payload or "{}")
        task_id_str = str(task.id)

        async def on_process_start(pid: int) -> None:
            self._active_task_pids[task_id_str] = pid
            async with self._session_factory() as db:
                await TaskRepository(db).update_process_pid(task.id, pid)
                await db.commit()

        if task.task_type == "run_command":
            command = payload.get("command", "")
            workspace = self._resolve_workspace(payload.get("workspace"))
            parts = command.split()
            result = await self._runner.run(
                parts, cwd=workspace, on_process_start=on_process_start
            )
            if result.timed_out:
                raise AgentTimeoutError(self._settings.task_timeout, result.stdout)
            if result.cancelled:
                return "Task cancelled."
            output = result.stdout
            if result.stderr:
                output = f"{output}\n\nstderr:\n{result.stderr}" if output else result.stderr
            return output or f"(exit {result.returncode})"

        if task.task_type == "deploy":
            deploy = DeployService(self._settings)
            context = await self._build_deploy_context(task)
            return await deploy.trigger(context)

        if task.task_type == "cursor_account_login":
            login = CursorAccountLoginService(
                self._settings, self._redis, self._notifier
            )
            account_id = str(payload.get("account_id", ""))
            telegram_id = int(payload.get("telegram_id", 0))
            return await login.start_login_and_notify(telegram_id, account_id)

        if task.task_type == "cursor_account_login_cancel":
            login = CursorAccountLoginService(
                self._settings, self._redis, self._notifier
            )
            telegram_id = int(payload.get("telegram_id", 0))
            return await login.cancel_login(telegram_id)

        if task.task_type == "refresh_models":
            result = await self._runner.run(
                [self._settings.cursor_agent_bin, "models"],
                sanitize_output=False,
            )
            if result.returncode != 0:
                raise RuntimeError(
                    f"cursor-agent models failed: {result.stderr or result.returncode}"
                )
            models = parse_models_output(result.stdout)
            if not models:
                raise RuntimeError("cursor-agent models returned no entries")
            write_models_catalog(self._settings, models)
            return f"Каталог моделей обновлён: {len(models)} шт. Открой /model заново."

        if task.task_type == "mcp_finalize":
            server_id = str(payload.get("server_id", ""))
            title = payload.get("title")
            async with self._session_factory() as db:
                service = McpSetupService(db, self._settings, self._runner)
                return await service.finalize_server(
                    server_id,
                    str(title) if title else None,
                )

        if task.task_type == "agent_prompt":
            return await self._run_agent_prompt(
                task,
                payload,
                on_progress=on_progress,
                on_process_start=on_process_start,
            )

        raise ValueError(f"Unknown task type: {task.task_type}")

    async def _run_agent_prompt(
        self,
        task: Task,
        payload: dict[str, object],
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_process_start: Callable[[int], Awaitable[None]] | None = None,
    ) -> str:
        accounts = CursorAccountService(self._settings, self._redis)
        switch_notice = ""
        proactive = await accounts.ensure_healthy_active_account()
        if proactive is not None:
            switch_notice = (
                f"Аккаунт Cursor переключён на `{proactive.id}` "
                f"({proactive.label}) — предыдущий недоступен.\n\n"
            )

        adapter = CursorAgentAdapter(self._settings, self._runner)
        workspace = self._resolve_workspace(
            payload.get("agent_workspace") or payload.get("workspace")
        )
        prompt = str(payload.get("prompt", ""))
        resume_chat_id = None
        if task.session_id:
            async with self._session_factory() as db:
                sessions = SessionRepository(db)
                agent_session = await sessions.get_by_id(task.session_id)
                if agent_session and agent_session.cursor_chat_id:
                    resume_chat_id = agent_session.cursor_chat_id

        active = await accounts.get_active_account()
        await accounts.activate_account(active)
        agent_result = await self._execute_agent_prompt(
            adapter,
            workspace,
            prompt,
            resume_chat_id,
            accounts.account_env(active),
            on_progress=on_progress,
            on_process_start=on_process_start,
        )

        failure = classify_agent_result(
            AgentRunOutcome(
                output=agent_result.output,
                stderr=agent_result.stderr,
                returncode=agent_result.returncode,
                cancelled=agent_result.cancelled,
            )
        )
        if is_account_switchable_failure(failure):
            next_account = await accounts.rotate_after_failure(active.id, failure.value)
            if next_account is not None:
                switch_notice = (
                    f"Лимит или сессия на `{active.id}` — переключилась на "
                    f"`{next_account.id}` ({next_account.label}).\n\n"
                )
                await accounts.activate_account(next_account)
                agent_result = await self._execute_agent_prompt(
                    adapter,
                    workspace,
                    prompt,
                    None,
                    accounts.account_env(next_account),
                    on_progress=on_progress,
                    on_process_start=on_process_start,
                )

        if agent_result.cursor_chat_id and task.session_id:
            # Persist first: a timed-out or cancelled session must stay resumable.
            async with self._session_factory() as db:
                sessions = SessionRepository(db)
                await sessions.update_cursor_chat_id(
                    task.session_id, agent_result.cursor_chat_id
                )
                await db.commit()

        if agent_result.cancelled:
            return "Task cancelled."
        if agent_result.timed_out:
            raise AgentTimeoutError(self._settings.task_timeout, agent_result.output)

        output = agent_result.output or "(no output)"
        if switch_notice:
            output = switch_notice + output
        return output

    async def _execute_agent_prompt(
        self,
        adapter: CursorAgentAdapter,
        workspace: str,
        prompt: str,
        resume_chat_id: str | None,
        process_env: dict[str, str],
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_process_start: Callable[[int], Awaitable[None]] | None = None,
    ) -> AgentResult:
        return await adapter.run_prompt(
            workspace,
            prompt,
            resume_chat_id,
            on_progress=on_progress,
            on_process_start=on_process_start,
            process_env=process_env,
        )

    async def _build_deploy_context(self, task: Task) -> DeployContext:
        async with self._session_factory() as db:
            users = UserRepository(db)
            sessions = SessionRepository(db)
            user = await users.get_by_id(task.user_id)
            agent_session = (
                await sessions.get_by_id(task.session_id) if task.session_id else None
            )

        workspace = None
        if agent_session:
            workspace = agent_session.workspace_path
        elif self._settings.agent_workspace is not None:
            workspace = str(self._settings.agent_workspace)
        else:
            workspace = str(self._settings.self_repo_root)

        return DeployContext(
            task_id=task.id,
            user_id=task.user_id,
            telegram_id=user.telegram_id if user else None,
            session_id=task.session_id,
            cursor_chat_id=agent_session.cursor_chat_id if agent_session else None,
            workspace=workspace,
        )

    def _resolve_workspace(self, value: object) -> str:
        workspace = str(value or self._settings.projects_root)
        # Bot containers use /workspace; the host worker sees the same bind
        # mount at PROJECTS_ROOT and never follows the container-only path.
        if workspace == "/workspace":
            return str(self._settings.projects_root)
        return workspace


def main() -> None:
    worker = TaskWorker()
    asyncio.run(worker.start())


if __name__ == "__main__":
    main()
