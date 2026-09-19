"""Task repository."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.models.task import Task


class TaskRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, task_id: uuid.UUID) -> Task | None:
        result = await self._session.execute(select(Task).where(Task.id == task_id))
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id: uuid.UUID,
        task_type: str,
        payload: str | None = None,
        session_id: uuid.UUID | None = None,
        project_id: uuid.UUID | None = None,
    ) -> Task:
        task = Task(
            user_id=user_id,
            task_type=task_type,
            payload=payload,
            session_id=session_id,
            project_id=project_id,
            status="pending",
        )
        self._session.add(task)
        await self._session.flush()
        return task

    async def mark_running(self, task_id: uuid.UUID, pid: int | None = None) -> Task | None:
        task = await self.get_by_id(task_id)
        if task is None:
            return None
        task.status = "running"
        task.started_at = datetime.now(UTC)
        task.process_pid = pid
        await self._session.flush()
        return task

    async def mark_completed(self, task_id: uuid.UUID, result: str) -> Task | None:
        task = await self.get_by_id(task_id)
        if task is None:
            return None
        task.status = "completed"
        task.result = result
        task.completed_at = datetime.now(UTC)
        await self._session.flush()
        return task

    async def mark_failed(self, task_id: uuid.UUID, error: str) -> Task | None:
        task = await self.get_by_id(task_id)
        if task is None:
            return None
        task.status = "failed"
        task.error = error
        task.completed_at = datetime.now(UTC)
        await self._session.flush()
        return task

    async def mark_cancelled(self, task_id: uuid.UUID) -> Task | None:
        task = await self.get_by_id(task_id)
        if task is None:
            return None
        task.status = "cancelled"
        task.completed_at = datetime.now(UTC)
        await self._session.flush()
        return task

    async def list_pending(self, limit: int = 10) -> list[Task]:
        result = await self._session.execute(
            select(Task)
            .where(Task.status == "pending")
            .order_by(Task.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_running_for_user(self, user_id: uuid.UUID) -> list[Task]:
        result = await self._session.execute(
            select(Task)
            .where(Task.user_id == user_id, Task.status == "running")
            .order_by(Task.started_at)
        )
        return list(result.scalars().all())

    async def update_process_pid(self, task_id: uuid.UUID, pid: int) -> Task | None:
        task = await self.get_by_id(task_id)
        if task is None:
            return None
        task.process_pid = pid
        await self._session.flush()
        return task
