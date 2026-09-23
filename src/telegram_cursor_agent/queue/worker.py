"""Background task worker."""

import asyncio
import json
import os
import signal
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from redis.asyncio import Redis

from telegram_cursor_agent.agent.adapter import AgentResult, CursorAgentAdapter
from telegram_cursor_agent.agent.prompts import build_redirect_prompt
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
from telegram_cursor_agent.services.cursor_accounts import (
    CursorAccountError,
    CursorAccountService,
)
from telegram_cursor_agent.services.cursor_models import (
    REFRESH_MODELS_MENU_UPDATED,
    catalog_from_models_output,
    load_models,
    models_catalog_is_present,
)
from telegram_cursor_agent.telegram.main_menu import cursor_submenu_keyboard
from telegram_cursor_agent.telegram.menu_navigation import (
    build_accounts_menu_view,
    build_cursor_submenu_text,
)
from telegram_cursor_agent.telegram.model_keyboards import (
    ModelPickerState,
    model_picker_keyboard,
    model_picker_text,
    picker_state_from_payload,
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
    mark_clean_shutdown,
    read_marker,
    update_marker_completed,
    update_marker_fields,
    write_marker,
)
from telegram_cursor_agent.services.mcp_config import McpConfigService
from telegram_cursor_agent.services.mcp_setup import McpSetupService
from telegram_cursor_agent.services.outbound_attachments import (
    OutboundAttachment,
    extract_outbound_attachments,
    format_skipped_attachments,
)
from telegram_cursor_agent.services.session_execution import (
    SessionExecState,
    SessionExecutionService,
)
from telegram_cursor_agent.telegram.live_message import (
    LiveMessageNotifier,
    THINKING_STATUS_TEXT,
)
from telegram_cursor_agent.telegram.session_live import (
    CONTINUE_STATUS_TEXT,
    REDIRECT_STATUS_TEXT,
    persist_live_message_ref,
)
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
        self._redis_pubsub: Redis | None = None  # type: ignore[type-arg]
        self._queue: TaskQueue | None = None
        self._notifier = TelegramNotifier(self._settings)
        self._active_task_pids: dict[str, int] = {}
        self._session_task_map: dict[str, str] = {}
        self._current_task_id: str | None = None
        self._last_watchdog_at = 0.0
        self._stop = asyncio.Event()
        self._shutdown_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        setup_logging(self._settings)
        self._redis = await create_redis(self._settings)
        self._redis_pubsub = await create_redis(self._settings)
        self._queue = TaskQueue(self._redis, pubsub_redis=self._redis_pubsub)
        await SessionExecutionService(self._redis).publish_owner()
        self._install_signal_handlers()
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
        await self._drain_orphan_redirects()
        await self._ensure_models_catalog()
        try:
            McpConfigService(self._settings).repair_local_config()
        except Exception:
            logger.exception("mcp_config_repair_failed")
        logger.info("worker_started")
        try:
            CursorAccountService(self._settings, self._redis).list_accounts()
        except Exception:
            logger.exception("accounts_auth_migration_failed")
        cancel_listener = asyncio.create_task(self._supervise_cancellations())
        redirect_listener = asyncio.create_task(self._supervise_session_redirects())

        failures = 0
        try:
            while not self._stop.is_set():
                try:
                    if self._queue is None:
                        await asyncio.sleep(1)
                        continue
                    task_id = await self._queue.dequeue(block_seconds=1)
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
            redirect_listener.cancel()
            await asyncio.gather(cancel_listener, redirect_listener, return_exceptions=True)
            await self._shutdown()
        logger.info("worker_restarting_after_deploy")

    async def _shutdown(self) -> None:
        for closer in (self._notifier.close, self._close_redis):
            try:
                await closer()
            except Exception:
                logger.warning("worker_shutdown_cleanup_failed", exc_info=True)

    async def _close_redis(self) -> None:
        for client in (self._redis, self._redis_pubsub):
            if client is not None:
                await client.aclose()  # type: ignore[attr-defined]

    async def _ensure_models_catalog(self) -> None:
        if models_catalog_is_present(self._settings):
            return
        try:
            models = await self._refresh_models_catalog_from_cli()
            logger.info("models_catalog_bootstrapped", models=len(models))
        except Exception:
            logger.exception("models_catalog_bootstrap_failed")

    async def _refresh_models_catalog_from_cli(self) -> list[dict[str, str]]:
        result = await self._runner.run(
            [self._settings.cursor_agent_bin, "models"],
            sanitize_output=False,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"cursor-agent models failed: {result.stderr or result.returncode}"
            )
        return catalog_from_models_output(self._settings, result.stdout)

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

    async def _supervise_session_redirects(self) -> None:
        while True:
            try:
                await self._listen_session_redirects()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("redirect_listener_error")
            await asyncio.sleep(1)

    async def _listen_session_redirects(self) -> None:
        if self._queue is None:
            return
        async for session_id in self._queue.listen_session_redirect():
            await self._interrupt_session_for_redirect(session_id)

    async def _interrupt_session_for_redirect(self, session_id: str) -> None:
        task_id = self._session_task_map.get(session_id)
        pid = self._active_task_pids.get(task_id) if task_id else None
        if pid is None and task_id is not None:
            try:
                async with self._session_factory() as db:
                    task = await TaskRepository(db).get_by_id(uuid.UUID(task_id))
                    if task is not None and task.process_pid:
                        pid = task.process_pid
            except Exception:
                logger.exception(
                    "redirect_process_pid_lookup_failed",
                    session_id=session_id,
                    task_id=task_id,
                )
        logger.info(
            "process_interrupting",
            session_id=session_id,
            task_id=task_id,
            pid=pid,
        )
        if pid is None:
            logger.warning(
                "redirect_no_process",
                session_id=session_id,
                task_id=task_id,
            )
            # This worker does not own the turn. A redirect with no process
            # used to return and leave the user's text in Redis forever.
            if task_id is None:
                try:
                    await self._promote_orphan_redirect(session_id)
                except Exception:
                    logger.exception(
                        "redirect_orphan_promote_failed",
                        session_id=session_id,
                    )
            return
        interrupted = await self._runner.cancel_pid(pid)
        if interrupted:
            logger.info(
                "process_interrupted",
                session_id=session_id,
                task_id=task_id,
            )
            return
        logger.warning(
            "redirect_failed",
            session_id=session_id,
            task_id=task_id,
            reason="cancel_pid_failed",
        )
        telegram_id: int | None = None
        if task_id is not None:
            try:
                async with self._session_factory() as db:
                    task = await TaskRepository(db).get_by_id(uuid.UUID(task_id))
                    if task is not None:
                        telegram_id = await self._lookup_telegram_id(task.user_id)
            except Exception:
                logger.exception("redirect_failed_lookup_user", task_id=task_id)
        await self._notify_safe(telegram_id, "⚠️ Не удалось прервать текущую задачу")

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, self._on_shutdown_signal)
            except (NotImplementedError, RuntimeError):
                logger.warning("shutdown_signal_handler_unavailable", signal=sig.name)
                return

    def _on_shutdown_signal(self) -> None:
        # Synchronous: systemd may SIGKILL before the async handler runs.
        try:
            mark_clean_shutdown(self._settings)
        except OSError:
            logger.exception("clean_shutdown_mark_failed")
        if self._shutdown_task is not None and not self._shutdown_task.done():
            return
        self._shutdown_task = asyncio.create_task(self._begin_shutdown())

    async def _begin_shutdown(self) -> None:
        """Release session ownership before systemd finishes killing us."""
        logger.info("worker_shutdown_requested")
        await self._release_owned_sessions()
        current = self._current_task_id
        pid = self._active_task_pids.get(current) if current else None
        if pid is not None:
            try:
                await self._runner.cancel_pid(pid)
            except Exception:
                logger.exception("shutdown_cancel_failed", pid=pid)
        self._stop.set()

    async def _release_owned_sessions(self) -> None:
        if self._redis is None:
            return
        session_exec = SessionExecutionService(self._redis)
        for session_id in list(self._session_task_map):
            try:
                await session_exec.release_if_owner(uuid.UUID(session_id), os.getpid())
            except Exception:
                logger.exception("release_session_failed", session_id=session_id)
        try:
            await session_exec.clear_owner()
        except Exception:
            logger.exception("clear_owner_failed")

    async def _drain_orphan_redirects(self) -> None:
        """Turn redirects published while this process was down into tasks."""
        if self._redis is None:
            return
        try:
            session_ids = await SessionExecutionService(
                self._redis
            ).list_redirect_session_ids()
        except Exception:
            logger.exception("orphan_redirect_scan_failed")
            return
        for session_id in session_ids:
            if str(session_id) in self._session_task_map:
                continue
            try:
                await self._promote_orphan_redirect(str(session_id))
            except Exception:
                logger.exception(
                    "redirect_orphan_promote_failed",
                    session_id=str(session_id),
                )

    async def _promote_orphan_redirect(self, session_id: str) -> None:
        """A redirect with no live process becomes an ordinary queued turn."""
        if self._redis is None or self._queue is None:
            return
        try:
            sid = uuid.UUID(session_id)
        except ValueError:
            return
        session_exec = SessionExecutionService(self._redis)
        # Drops a lease whose worker is already dead. A still-live owner
        # keeps the payload and consumes it inside its own turn.
        if await session_exec.get_running_task_id(sid) is not None:
            logger.info("redirect_left_for_live_owner", session_id=session_id)
            return
        pending = await session_exec.consume_pending_redirect(sid)
        if not pending:
            logger.warning("redirect_orphan_empty", session_id=session_id)
            return
        prompt = str(pending.get("prompt", "")).strip()
        if not prompt:
            return
        async with self._session_factory() as db:
            agent_session = await SessionRepository(db).get_by_id(sid)
            if agent_session is None:
                logger.warning("redirect_orphan_no_session", session_id=session_id)
                return
            tasks = TaskRepository(db)
            for stale in await tasks.list_running_agent_for_session(sid):
                if str(stale.id) == self._current_task_id:
                    continue
                await tasks.mark_failed(
                    stale.id,
                    "Stale running turn replaced by a queued message.",
                )
            workspace = (
                pending.get("agent_workspace")
                or pending.get("workspace")
                or agent_session.workspace_path
            )
            payload = {
                "prompt": prompt,
                "workspace": pending.get("workspace") or agent_session.workspace_path,
                "agent_workspace": self._settings.normalize_cursor_workspace(
                    str(workspace) if workspace is not None else None
                ),
            }
            payload_json = json.dumps(payload, ensure_ascii=False)
            pending_tasks = await tasks.list_pending_agent_for_session(sid)
            if pending_tasks:
                primary = pending_tasks[0]
                for extra in pending_tasks[1:]:
                    await tasks.mark_cancelled(extra.id)
                await tasks.update_payload(primary.id, payload_json)
                queued_id = str(primary.id)
            else:
                created = await tasks.create(
                    user_id=agent_session.user_id,
                    task_type="agent_prompt",
                    payload=payload_json,
                    session_id=sid,
                    project_id=agent_session.project_id,
                )
                queued_id = str(created.id)
            await db.commit()
        await self._queue.enqueue(queued_id)
        logger.info(
            "redirect_orphan_promoted",
            session_id=session_id,
            task_id=queued_id,
        )

    async def _recover_pending_tasks(self) -> None:
        async with self._session_factory() as db:
            pending = await TaskRepository(db).list_pending(limit=10)
        for task in pending:
            await self._process_task(str(task.id))

    async def _process_task(self, task_id_str: str) -> None:
        task_id = uuid.UUID(task_id_str)
        claimed = await self._claim_task(task_id)
        if claimed is None:
            if self._queue is not None:
                try:
                    async with self._session_factory() as db:
                        orphan = await TaskRepository(db).get_by_id(task_id)
                    if orphan is not None and orphan.status == "pending":
                        await self._queue.enqueue(task_id_str)
                except Exception:
                    logger.exception("requeue_orphan_task_failed", task_id=task_id_str)
            return
        task, telegram_id, show_tool_calls_live = claimed
        self._current_task_id = task_id_str
        if task.session_id is not None and task.task_type == "agent_prompt":
            self._session_task_map[str(task.session_id)] = task_id_str
            if self._redis is not None:
                session_exec = SessionExecutionService(self._redis)
                await session_exec.register_running_task(task.session_id, task.id)
                await session_exec.set_state(
                    task.session_id, SessionExecState.RUNNING
                )
        try:
            await self._run_claimed_task(
                task, telegram_id, show_tool_calls_live=show_tool_calls_live
            )
        except Exception as exc:
            # Safety net: whatever fails, the task never stays "running" in silence.
            logger.exception("task_failed", task_id=task_id_str)
            await self._fail_task(task_id, telegram_id, exc)
        finally:
            if task.session_id is not None:
                self._session_task_map.pop(str(task.session_id), None)
            self._current_task_id = None
            self._active_task_pids.pop(task_id_str, None)

    async def _claim_task(
        self, task_id: uuid.UUID
    ) -> tuple[Task, int | None, bool] | None:
        """Mark the task running in a short transaction and snapshot what we need."""
        async with self._session_factory() as db:
            tasks = TaskRepository(db)
            task = await tasks.get_by_id(task_id)
            if task is None or task.status != "pending":
                return None
            await tasks.mark_running(task_id)
            user = await UserRepository(db).get_by_id(task.user_id)
            telegram_id = user.telegram_id if user is not None else None
            show_tool_calls_live = (
                bool(user.show_tool_calls_live) if user is not None else False
            )
            await db.commit()
        return task, telegram_id, show_tool_calls_live

    async def _run_claimed_task(
        self,
        task: Task,
        telegram_id: int | None,
        *,
        show_tool_calls_live: bool = False,
    ) -> None:
        task_id = task.id
        task_id_str = str(task_id)
        payload = json.loads(task.payload or "{}")
        silent_recovery = bool(payload.get("silent_recovery"))
        is_system_recovery = bool(
            payload.get("deploy_recovery") or payload.get("interrupt_recovery")
        )
        skip_typing = (
            task.task_type
            in {
                "cursor_account_login",
                "cursor_account_login_cancel",
                "cursor_account_switch",
                "deploy",
            }
            or silent_recovery
            or is_system_recovery
        )
        typing_task = (
            asyncio.create_task(self._notifier.keep_typing(telegram_id))
            if telegram_id is not None and not skip_typing
            else None
        )
        if typing_task is not None:
            await self._notifier.send_typing_once(telegram_id)
        live: LiveMessageNotifier | None = None

        async def stop_typing() -> None:
            nonlocal typing_task
            if typing_task is None:
                return
            typing_task.cancel()
            await asyncio.gather(typing_task, return_exceptions=True)
            typing_task = None

        async def refresh_typing() -> None:
            if typing_task is not None and telegram_id is not None:
                await self._notifier.send_typing_once(telegram_id)

        try:
            if (
                telegram_id is not None
                and task.task_type == "agent_prompt"
                and not silent_recovery
            ):
                live = LiveMessageNotifier(
                    self._notifier,
                    self._settings,
                    telegram_id,
                    tool_calls_in_live=show_tool_calls_live,
                )
                await live.update_status(THINKING_STATUS_TEXT)
                await refresh_typing()

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

            live_ref_persisted = False

            async def _maybe_persist_live_ref() -> None:
                nonlocal live_ref_persisted
                if (
                    live_ref_persisted
                    or live is None
                    or live.message_id is None
                    or task.session_id is None
                    or self._redis is None
                ):
                    return
                await persist_live_message_ref(
                    self._redis,
                    task.session_id,
                    telegram_id,
                    live.message_id,
                )
                live_ref_persisted = True

            async def on_progress(text: str) -> None:
                if live is not None:
                    if show_tool_calls_live:
                        await live.replace_display(text)
                    else:
                        await live.update(text)
                    await refresh_typing()
                    await _maybe_persist_live_ref()

            async def on_status(text: str) -> None:
                if live is not None:
                    await live.update_status(text)
                    await refresh_typing()
                    await _maybe_persist_live_ref()

            try:
                result = await self._execute(
                    task,
                    on_progress if live is not None else None,
                    telegram_id=telegram_id,
                    on_status=on_status if live is not None else None,
                    show_tool_calls_live=show_tool_calls_live,
                )
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

            attachments: list[OutboundAttachment] = []
            if task.task_type == "deploy":
                user_message = DEPLOY_SUCCESS_MESSAGE
            else:
                parsed = extract_outbound_attachments(result, self._settings)
                user_message = parsed.text + format_skipped_attachments(parsed.skipped)
                attachments = parsed.attachments
                if not user_message.strip() and attachments:
                    user_message = "📎 Файлы во вложении."
            # Persist before delivering: a Telegram outage must not discard the result.
            await self._persist_status(
                task_id, lambda repo: repo.mark_completed(task_id, user_message)
            )
            if (
                telegram_id is not None
                and not silent_recovery
                and task.task_type != "cursor_account_login_cancel"
                and task.task_type != "deploy"
                and result != CURSOR_LOGIN_TASK_NOTIFIED
                and result != REFRESH_MODELS_MENU_UPDATED
            ):
                await self._deliver_result(
                    live, telegram_id, user_message, attachments=attachments
                )
        finally:
            await stop_typing()

    def _clear_session_marker(self, task: Task, is_system_recovery: bool) -> None:
        if task.task_type != "agent_prompt" or is_system_recovery:
            return
        marker = read_marker(self._settings)
        if marker is None or marker.status != SESSION_ACTIVE_STATUS:
            return
        if marker.task_id == str(task.id):
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
        self,
        live: LiveMessageNotifier | None,
        telegram_id: int,
        text: str,
        *,
        attachments: list[OutboundAttachment] | None = None,
    ) -> None:
        files = attachments or []
        if live is not None and live.message_id is not None:
            try:
                await live.flush_pending_display()
                await live.finalize(text)
                if files:
                    await self._send_attachments_safe(telegram_id, files)
                return
            except Exception:
                # Editing can fail (message deleted, too long) — fall back to a new message.
                logger.warning("live_finalize_failed", telegram_id=telegram_id)
        await self._notify_safe(telegram_id, text)
        if files:
            await self._send_attachments_safe(telegram_id, files)

    async def _send_attachments_safe(
        self, telegram_id: int, attachments: list[OutboundAttachment]
    ) -> None:
        try:
            errors = await self._notifier.send_attachments(telegram_id, attachments)
        except Exception:
            logger.exception("send_attachments_failed", telegram_id=telegram_id)
            await self._notify_safe(
                telegram_id,
                "⚠️ Не удалось отправить прикреплённые файлы — см. логи worker.",
            )
            return
        if errors:
            await self._notify_safe(
                telegram_id,
                "⚠️ Часть файлов не отправилась:\n" + "\n".join(errors),
            )

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
            if (
                marker is not None
                and marker.status == SESSION_ACTIVE_STATUS
                and marker.task_id == str(task.id)
            ):
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
        self,
        task: Task,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        telegram_id: int | None = None,
        on_status: Callable[[str], Awaitable[None]] | None = None,
        *,
        show_tool_calls_live: bool = False,
    ) -> str:
        payload = json.loads(task.payload or "{}")
        task_id_str = str(task.id)

        async def on_process_start(pid: int) -> None:
            self._active_task_pids[task_id_str] = pid
            if task.session_id is not None and self._redis is not None:
                await SessionExecutionService(self._redis).register_running_task(
                    task.session_id, task.id
                )
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

        if task.task_type == "cursor_account_switch":
            accounts = CursorAccountService(self._settings, self._redis)
            account_id = str(payload.get("account_id", ""))
            telegram_id = int(payload.get("telegram_id", 0))
            try:
                account = await accounts.set_active_account(account_id)
            except CursorAccountError as exc:
                await self._notify_safe(telegram_id, str(exc))
                raise
            menu_raw = payload.get("menu_message")
            if isinstance(menu_raw, dict) and telegram_id:
                chat_id = int(menu_raw["chat_id"])
                message_id = int(menu_raw["message_id"])
                try:
                    text, markup = await build_accounts_menu_view(
                        self._settings, self._redis
                    )
                    await self._notifier.edit_live_message(
                        chat_id,
                        message_id,
                        text,
                        reply_markup=markup,
                    )
                except Exception:
                    logger.exception("account_switch_menu_edit_failed")
                    await self._notify_safe(
                        telegram_id,
                        f"Активный аккаунт Cursor: *{account.label}* (`{account.id}`)",
                    )
            else:
                await self._notify_safe(
                    telegram_id,
                    f"Активный аккаунт Cursor: *{account.label}* (`{account.id}`)",
                )
            return CURSOR_LOGIN_TASK_NOTIFIED

        if task.task_type == "refresh_models":
            models = await self._refresh_models_catalog_from_cli()
            menu_raw = payload.get("menu_message")
            if isinstance(menu_raw, dict):
                chat_id = int(menu_raw["chat_id"])
                message_id = int(menu_raw["message_id"])
                view = str(menu_raw.get("view", "picker"))
                try:
                    if view == "picker":
                        state = picker_state_from_payload(menu_raw)
                        if state is None:
                            state = ModelPickerState(menu=True)
                        catalog_models = load_models(self._settings)
                        text = model_picker_text(
                            catalog_models, state, self._settings
                        )
                        markup = model_picker_keyboard(
                            catalog_models, state, self._settings
                        )
                    else:
                        text = (
                            f"{build_cursor_submenu_text(self._settings)}\n\n"
                            f"✅ Список обновлён ({len(models)} моделей)."
                        )
                        markup = cursor_submenu_keyboard()
                    await self._notifier.edit_live_message(
                        chat_id,
                        message_id,
                        text,
                        reply_markup=markup,
                    )
                except Exception:
                    logger.exception("refresh_models_menu_edit_failed")
                return REFRESH_MODELS_MENU_UPDATED
            return f"Каталог моделей обновлён: {len(models)} шт."

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
                telegram_id=telegram_id,
                on_progress=on_progress,
                on_status=on_status,
                on_process_start=on_process_start,
                show_tool_calls_live=show_tool_calls_live,
            )

        raise ValueError(f"Unknown task type: {task.task_type}")

    async def _run_agent_prompt(
        self,
        task: Task,
        payload: dict[str, object],
        telegram_id: int | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_status: Callable[[str], Awaitable[None]] | None = None,
        on_process_start: Callable[[int], Awaitable[None]] | None = None,
        *,
        show_tool_calls_live: bool = False,
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
        resume_chat_id = payload.get("cursor_chat_id")
        if resume_chat_id is not None:
            resume_chat_id = str(resume_chat_id).strip() or None
        session_id = task.session_id
        session_exec = (
            SessionExecutionService(self._redis)
            if self._redis is not None and session_id is not None
            else None
        )

        if session_id and resume_chat_id is None:
            async with self._session_factory() as db:
                sessions = SessionRepository(db)
                agent_session = await sessions.get_by_id(session_id)
                if agent_session and agent_session.cursor_chat_id:
                    resume_chat_id = agent_session.cursor_chat_id

        redirect_iteration = 0
        output = "(no output)"

        try:
            while True:
                if session_exec is not None and session_id is not None:
                    await session_exec.set_state(session_id, SessionExecState.RUNNING)
                if redirect_iteration == 0:
                    logger.info(
                        "task_started",
                        session_id=str(session_id),
                        task_id=str(task.id),
                    )
                else:
                    if on_status is not None:
                        await on_status(CONTINUE_STATUS_TEXT)
                    logger.info(
                        "redirect_started",
                        session_id=str(session_id),
                        task_id=str(task.id),
                        iteration=redirect_iteration,
                    )

                active = await accounts.get_active_account()
                await accounts.activate_account(active)
                agent_result = await self._execute_agent_prompt(
                    adapter,
                    workspace,
                    prompt,
                    resume_chat_id,
                    accounts.account_env(active),
                    task=task,
                    on_progress=on_progress,
                    on_status=on_status,
                    on_process_start=on_process_start,
                    show_tool_calls_live=show_tool_calls_live,
                    telegram_id=telegram_id,
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
                    next_account = await accounts.rotate_after_failure(
                        active.id, failure.value
                    )
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
                            resume_chat_id,
                            accounts.account_env(next_account),
                            task=task,
                            on_progress=on_progress,
                            on_status=on_status,
                            on_process_start=on_process_start,
                            show_tool_calls_live=show_tool_calls_live,
                            telegram_id=telegram_id,
                        )

                if agent_result.cursor_chat_id and session_id:
                    async with self._session_factory() as db:
                        sessions = SessionRepository(db)
                        await sessions.update_cursor_chat_id(
                            session_id, agent_result.cursor_chat_id
                        )
                        await db.commit()
                    resume_chat_id = agent_result.cursor_chat_id
                    update_marker_fields(
                        self._settings,
                        cursor_chat_id=agent_result.cursor_chat_id,
                        session_id=str(session_id),
                    )

                pending = (
                    await session_exec.consume_pending_redirect(session_id)
                    if session_exec is not None and session_id is not None
                    else None
                )
                if pending:
                    redirect_iteration += 1
                    prompt = build_redirect_prompt(str(pending.get("prompt", "")))
                    workspace = self._resolve_workspace(
                        pending.get("agent_workspace")
                        or pending.get("workspace")
                        or workspace
                    )
                    if session_exec is not None and session_id is not None:
                        await session_exec.set_state(
                            session_id, SessionExecState.REDIRECTING
                        )
                    logger.info(
                        "redirect_completed",
                        session_id=str(session_id),
                        task_id=str(task.id),
                    )
                    continue

                if session_exec is not None and session_id is not None:
                    pending_late = await session_exec.consume_pending_redirect(
                        session_id
                    )
                    if pending_late:
                        redirect_iteration += 1
                        prompt = build_redirect_prompt(
                            str(pending_late.get("prompt", ""))
                        )
                        workspace = self._resolve_workspace(
                            pending_late.get("agent_workspace")
                            or pending_late.get("workspace")
                            or workspace
                        )
                        await session_exec.set_state(
                            session_id, SessionExecState.REDIRECTING
                        )
                        logger.info(
                            "redirect_completed",
                            session_id=str(session_id),
                            task_id=str(task.id),
                            source="late",
                        )
                        continue

                if agent_result.cancelled:
                    pending_on_cancel = (
                        await session_exec.consume_pending_redirect(session_id)
                        if session_exec is not None and session_id is not None
                        else None
                    )
                    if pending_on_cancel:
                        redirect_iteration += 1
                        prompt = build_redirect_prompt(
                            str(pending_on_cancel.get("prompt", ""))
                        )
                        workspace = self._resolve_workspace(
                            pending_on_cancel.get("agent_workspace")
                            or pending_on_cancel.get("workspace")
                            or workspace
                        )
                        await session_exec.set_state(
                            session_id, SessionExecState.REDIRECTING
                        )
                        logger.info(
                            "redirect_completed",
                            session_id=str(session_id),
                            task_id=str(task.id),
                            source="cancel",
                        )
                        continue
                    return "Task cancelled."
                if agent_result.timed_out:
                    raise AgentTimeoutError(
                        self._settings.task_timeout, agent_result.output
                    )

                output = agent_result.output or "(no output)"
                break
        finally:
            if session_exec is not None and session_id is not None:
                await session_exec.release_if_owner(session_id, os.getpid())

        logger.info(
            "task_finished",
            session_id=str(session_id),
            task_id=str(task.id),
        )
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
        task: Task | None = None,
        on_progress: Callable[[str], Awaitable[None]] | None = None,
        on_status: Callable[[str], Awaitable[None]] | None = None,
        on_process_start: Callable[[int], Awaitable[None]] | None = None,
        *,
        show_tool_calls_live: bool = False,
        telegram_id: int | None = None,
    ) -> AgentResult:
        session_id = task.session_id if task is not None else None
        task_id_str = str(task.id) if task is not None else None
        if self._redis is None or session_id is None or task is None:
            return await adapter.run_prompt(
                workspace,
                prompt,
                resume_chat_id,
                on_progress=on_progress,
                on_process_start=on_process_start,
                process_env=process_env,
                show_tool_calls_live=show_tool_calls_live,
                telegram_id=telegram_id,
            )

        session_exec = SessionExecutionService(self._redis)
        poll_stop = asyncio.Event()
        redirect_status_sent = False

        async def poll_redirect() -> None:
            nonlocal redirect_status_sent
            while not poll_stop.is_set():
                try:
                    await asyncio.wait_for(poll_stop.wait(), timeout=0.25)
                    return
                except TimeoutError:
                    pass
                pending = await session_exec.peek_pending_redirect(session_id)
                if pending is None:
                    redirect_status_sent = False
                    continue
                if on_status is not None and not redirect_status_sent:
                    await on_status(REDIRECT_STATUS_TEXT)
                    redirect_status_sent = True
                logger.info(
                    "redirect_requested",
                    session_id=str(session_id),
                    task_id=task_id_str,
                    source="poll",
                )
                pid = (
                    self._active_task_pids.get(task_id_str)
                    if task_id_str is not None
                    else None
                )
                if pid is None:
                    async with self._session_factory() as db:
                        stored = await TaskRepository(db).get_by_id(task.id)
                        if stored is not None and stored.process_pid:
                            pid = stored.process_pid
                if pid is None:
                    logger.warning(
                        "redirect_no_process",
                        session_id=str(session_id),
                        task_id=task_id_str,
                    )
                    continue
                logger.info(
                    "process_interrupting",
                    session_id=str(session_id),
                    task_id=task_id_str,
                    pid=pid,
                    source="poll",
                )
                if await self._runner.cancel_pid(pid):
                    logger.info(
                        "process_interrupted",
                        session_id=str(session_id),
                        task_id=task_id_str,
                        source="poll",
                    )
                else:
                    logger.warning(
                        "redirect_failed",
                        session_id=str(session_id),
                        task_id=task_id_str,
                        reason="cancel_pid_failed",
                    )

        poll_task = asyncio.create_task(poll_redirect())
        try:
            return await adapter.run_prompt(
                workspace,
                prompt,
                resume_chat_id,
                on_progress=on_progress,
                on_process_start=on_process_start,
                process_env=process_env,
                show_tool_calls_live=show_tool_calls_live,
                telegram_id=telegram_id,
            )
        finally:
            poll_stop.set()
            poll_task.cancel()
            await asyncio.gather(poll_task, return_exceptions=True)

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
            workspace = self._settings.normalize_cursor_workspace(
                agent_session.workspace_path
            )
        elif self._settings.agent_workspace is not None:
            workspace = self._settings.normalize_cursor_workspace(
                self._settings.agent_workspace
            )
        else:
            workspace = self._settings.normalize_cursor_workspace(
                self._settings.self_repo_root
            )

        return DeployContext(
            task_id=task.id,
            user_id=task.user_id,
            telegram_id=user.telegram_id if user else None,
            session_id=task.session_id,
            cursor_chat_id=agent_session.cursor_chat_id if agent_session else None,
            workspace=workspace,
        )

    def _resolve_workspace(self, value: object) -> str:
        return self._settings.normalize_cursor_workspace(
            str(value) if value is not None else None
        )


def main() -> None:
    worker = TaskWorker()
    asyncio.run(worker.start())


if __name__ == "__main__":
    main()
