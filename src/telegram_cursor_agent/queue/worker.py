"""Background task worker."""

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis

from telegram_cursor_agent.agent.adapter import CursorAgentAdapter
from telegram_cursor_agent.core.config import get_settings
from telegram_cursor_agent.core.logging import get_logger, setup_logging
from telegram_cursor_agent.database.models.task import Task
from telegram_cursor_agent.database.repositories.session import SessionRepository
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.database.session import create_engine, create_session_factory
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.queue.task_queue import TaskQueue, create_redis
from telegram_cursor_agent.telegram.notifier import TelegramNotifier

logger = get_logger(__name__)


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

    async def start(self) -> None:
        setup_logging(self._settings)
        self._redis = await create_redis(self._settings)
        self._queue = TaskQueue(self._redis)
        logger.info("worker_started")
        cancel_listener = asyncio.create_task(self._listen_cancellations())

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
            except Exception:
                logger.exception("worker_loop_error")
                await asyncio.sleep(1)
        cancel_listener.cancel()
        await asyncio.gather(cancel_listener, return_exceptions=True)

    async def _listen_cancellations(self) -> None:
        if self._queue is None:
            return
        try:
            async for task_id in self._queue.listen_cancel():
                pid = self._active_task_pids.get(task_id)
                if pid is not None:
                    await self._runner.cancel_pid(pid)
                    continue
                async with self._session_factory() as db:
                    task = await TaskRepository(db).get_by_id(uuid.UUID(task_id))
                    if task and task.process_pid:
                        await self._runner.cancel_pid(task.process_pid)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("cancel_listener_error")

    async def _recover_pending_tasks(self) -> None:
        async with self._session_factory() as db:
            pending = await TaskRepository(db).list_pending(limit=10)
        for task in pending:
            await self._process_task(str(task.id))

    async def _process_task(self, task_id_str: str) -> None:
        task_id = uuid.UUID(task_id_str)
        async with self._session_factory() as db:
            tasks = TaskRepository(db)
            task = await tasks.get_by_id(task_id)
            if task is None or task.status != "pending":
                return

            await tasks.mark_running(task_id)
            await db.commit()
            user = await UserRepository(db).get_by_id(task.user_id)
            typing_task = (
                asyncio.create_task(self._notifier.keep_typing(user.telegram_id))
                if user is not None
                else None
            )

            task_id_str = str(task_id)
            try:
                result = await self._execute(
                    task,
                    (lambda text: self._notifier.send(user.telegram_id, text)) if user else None,
                )
                if result == "Task cancelled.":
                    await tasks.mark_cancelled(task_id)
                    if user is not None:
                        await self._notifier.send(user.telegram_id, "Задача отменена.")
                else:
                    await tasks.mark_completed(task_id, result)
                    if user is not None:
                        await self._notifier.send(user.telegram_id, result)
            except Exception as exc:
                logger.exception("task_failed", task_id=task_id_str)
                await tasks.mark_failed(task_id, str(exc))
                if user is not None:
                    await self._notifier.send(
                        user.telegram_id, f"Cursor завершился с ошибкой:\n{exc}"
                    )
            finally:
                self._active_task_pids.pop(task_id_str, None)
                if typing_task is not None:
                    typing_task.cancel()
                    await asyncio.gather(typing_task, return_exceptions=True)
            await db.commit()

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
            if result.cancelled:
                return "Task cancelled."
            output = result.stdout
            if result.stderr:
                output = f"{output}\n\nstderr:\n{result.stderr}" if output else result.stderr
            return output or f"(exit {result.returncode})"

        if task.task_type == "agent_prompt":
            adapter = CursorAgentAdapter(self._settings, self._runner)
            workspace = self._resolve_workspace(payload.get("workspace"))
            prompt = payload.get("prompt", "")
            resume_chat_id = None
            if task.session_id:
                async with self._session_factory() as db:
                    sessions = SessionRepository(db)
                    agent_session = await sessions.get_by_id(task.session_id)
                    if agent_session and agent_session.cursor_chat_id:
                        resume_chat_id = agent_session.cursor_chat_id

            agent_result = await adapter.run_prompt(
                workspace,
                prompt,
                resume_chat_id,
                on_progress=on_progress,
                on_process_start=on_process_start,
            )
            if agent_result.cancelled:
                return "Task cancelled."
            if agent_result.cursor_chat_id and task.session_id:
                async with self._session_factory() as db:
                    sessions = SessionRepository(db)
                    await sessions.update_cursor_chat_id(
                        task.session_id, agent_result.cursor_chat_id
                    )
                    await db.commit()

            return agent_result.output or "(no output)"

        raise ValueError(f"Unknown task type: {task.task_type}")

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
