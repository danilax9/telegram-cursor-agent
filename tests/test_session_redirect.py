"""Session redirect / interrupt coordination tests."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.prompts import build_redirect_prompt
from telegram_cursor_agent.agent.sessions import SessionService
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.services.actions import ActionResultType, ActionService
from telegram_cursor_agent.services.session_execution import (
    SessionExecState,
    SessionExecutionService,
)


@pytest.fixture
def mock_queue() -> MagicMock:
    queue = MagicMock()
    queue.enqueue = AsyncMock()
    queue.publish_session_redirect = AsyncMock()
    queue.publish_cancel = AsyncMock()
    return queue


@pytest.fixture
def mock_redis() -> MagicMock:
    redis = MagicMock()
    store: dict[str, bytes | str] = {}

    async def get(key: str):
        return store.get(key)

    async def set(key: str, value, ex=None):
        store[key] = value if isinstance(value, bytes) else str(value).encode()

    async def delete(key: str):
        store.pop(key, None)

    async def exists(key: str):
        return 1 if key in store else 0

    async def getdel(key: str):
        return store.pop(key, None)

    async def publish(channel: str, message: str):
        return 1

    redis.get = AsyncMock(side_effect=get)
    redis.set = AsyncMock(side_effect=set)
    redis.delete = AsyncMock(side_effect=delete)
    redis.exists = AsyncMock(side_effect=exists)
    redis.getdel = AsyncMock(side_effect=getdel)
    redis.publish = AsyncMock(side_effect=publish)
    redis._store = store
    return redis


async def _user_and_session(
    db_session: AsyncSession, test_settings, workspace: str
) -> tuple[uuid.UUID, uuid.UUID]:
    user = await UserRepository(db_session).upsert(
        telegram_id=999, username="redirect", is_admin=True
    )
    session = await SessionService(db_session, test_settings).get_or_create_active(
        user.id, workspace, None
    )
    return user.id, session.id


async def test_build_redirect_prompt_prefix() -> None:
    text = build_redirect_prompt("сделай B")
    assert "Предыдущая задача была прервана" in text
    assert "сделай B" in text


async def test_queue_agent_when_idle(
    db_session: AsyncSession,
    test_settings,
    runner: ProcessRunner,
    mock_queue,
    mock_redis,
) -> None:
    user_id, _session_id = await _user_and_session(
        db_session, test_settings, str(test_settings.projects_root)
    )
    service = ActionService(
        db_session, test_settings, runner, mock_queue, mock_redis
    )
    result = await service.handle_text(
        user_id, "first task", str(test_settings.projects_root)
    )
    assert result.result_type == ActionResultType.TASK_QUEUED
    mock_queue.enqueue.assert_awaited_once()
    mock_redis.publish.assert_not_awaited()


async def test_redirect_when_session_running(
    db_session: AsyncSession,
    test_settings,
    runner: ProcessRunner,
    mock_queue,
    mock_redis,
) -> None:
    user_id, session_id = await _user_and_session(
        db_session, test_settings, str(test_settings.projects_root)
    )
    running = await TaskRepository(db_session).create(
        user_id=user_id,
        task_type="agent_prompt",
        payload=json.dumps({"prompt": "slow"}),
        session_id=session_id,
    )
    await TaskRepository(db_session).mark_running(running.id)
    exec_svc = SessionExecutionService(mock_redis)
    await exec_svc.register_running_task(session_id, running.id)
    await exec_svc.set_state(session_id, SessionExecState.RUNNING)

    service = ActionService(
        db_session, test_settings, runner, mock_queue, mock_redis
    )
    result = await service.handle_text(
        user_id, "new instruction", str(test_settings.projects_root)
    )
    assert result.result_type == ActionResultType.REDIRECT_REQUESTED
    assert result.task_id == running.id
    mock_queue.enqueue.assert_not_awaited()
    mock_queue.publish_cancel.assert_awaited_once_with(str(running.id))
    pending = await exec_svc.peek_pending_redirect(session_id)
    assert pending is not None
    assert pending["prompt"] == "new instruction"


async def test_coalesce_pending_task_payload(
    db_session: AsyncSession,
    test_settings,
    runner: ProcessRunner,
    mock_queue,
    mock_redis,
) -> None:
    user_id, session_id = await _user_and_session(
        db_session, test_settings, str(test_settings.projects_root)
    )
    pending_task = await TaskRepository(db_session).create(
        user_id=user_id,
        task_type="agent_prompt",
        payload=json.dumps({"prompt": "old"}),
        session_id=session_id,
    )
    service = ActionService(
        db_session, test_settings, runner, mock_queue, mock_redis
    )
    result = await service.handle_text(
        user_id, "updated instruction", str(test_settings.projects_root)
    )
    assert result.result_type == ActionResultType.TASK_QUEUED
    assert result.task_id == pending_task.id
    mock_queue.enqueue.assert_not_awaited()
    task = await TaskRepository(db_session).get_by_id(pending_task.id)
    assert task is not None
    payload = json.loads(task.payload or "{}")
    assert payload["prompt"] == "updated instruction"
