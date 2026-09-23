"""Session redirect / interrupt coordination tests."""

import fnmatch
import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_cursor_agent.agent.prompts import build_redirect_prompt
from telegram_cursor_agent.agent.sessions import SessionService
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.queue.worker import TaskWorker
from telegram_cursor_agent.services.actions import ActionResultType, ActionService
from telegram_cursor_agent.services.session_execution import (
    SESSION_RUNNING_TASK_KEY,
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

    async def scan_iter(match=None, count=100):
        for key in list(store):
            text = key.decode() if isinstance(key, bytes) else str(key)
            if match is not None and not fnmatch.fnmatch(text, match):
                continue
            yield text

    redis.get = AsyncMock(side_effect=get)
    redis.set = AsyncMock(side_effect=set)
    redis.delete = AsyncMock(side_effect=delete)
    redis.exists = AsyncMock(side_effect=exists)
    redis.getdel = AsyncMock(side_effect=getdel)
    redis.publish = AsyncMock(side_effect=publish)
    redis.scan_iter = scan_iter
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


async def test_stale_running_task_is_queued_not_redirected(
    db_session: AsyncSession,
    test_settings,
    runner: ProcessRunner,
    mock_queue,
    mock_redis,
) -> None:
    """A task id left in Redis after SIGTERM must not swallow the next message."""
    user_id, session_id = await _user_and_session(
        db_session, test_settings, str(test_settings.projects_root)
    )
    running = await TaskRepository(db_session).create(
        user_id=user_id,
        task_type="agent_prompt",
        payload=json.dumps({"prompt": "old turn"}),
        session_id=session_id,
    )
    await TaskRepository(db_session).mark_running(running.id)
    exec_svc = SessionExecutionService(mock_redis)
    await exec_svc.set_state(session_id, SessionExecState.RUNNING)
    await mock_redis.set(
        SESSION_RUNNING_TASK_KEY.format(session_id=session_id),
        str(running.id),
    )

    service = ActionService(
        db_session, test_settings, runner, mock_queue, mock_redis
    )
    result = await service.handle_text(
        user_id, "are you there", str(test_settings.projects_root)
    )

    assert result.result_type == ActionResultType.TASK_QUEUED
    assert result.task_id != running.id
    mock_queue.enqueue.assert_awaited_once()
    mock_redis.publish.assert_not_awaited()
    stale = await TaskRepository(db_session).get_by_id(running.id)
    assert stale is not None
    assert stale.status == "failed"
    assert await exec_svc.get_state(session_id) == SessionExecState.IDLE
    assert await exec_svc.get_running_task_id(session_id) is None


async def test_dead_owner_lease_is_not_a_running_turn(
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
        payload=json.dumps({"prompt": "old turn"}),
        session_id=session_id,
    )
    await TaskRepository(db_session).mark_running(running.id)
    dead_pid = 2**22
    lease = json.dumps(
        {"epoch": "old-epoch", "pid": dead_pid, "task_id": str(running.id)}
    )
    await mock_redis.set("tca:worker:owner", json.dumps({"epoch": "old-epoch", "pid": dead_pid}))
    await mock_redis.set(
        SESSION_RUNNING_TASK_KEY.format(session_id=session_id),
        str(running.id),
    )
    await mock_redis.set(
        f"tca:session:{session_id}:running_owner",
        lease,
    )

    service = ActionService(
        db_session, test_settings, runner, mock_queue, mock_redis
    )
    result = await service.handle_text(
        user_id, "continue", str(test_settings.projects_root)
    )

    assert result.result_type == ActionResultType.TASK_QUEUED
    mock_queue.enqueue.assert_awaited_once()
    mock_redis.publish.assert_not_awaited()


def _worker(settings, session_factory, redis, queue) -> TaskWorker:
    worker = object.__new__(TaskWorker)
    worker._settings = settings
    worker._session_factory = session_factory
    worker._redis = redis
    worker._queue = queue
    worker._session_task_map = {}
    worker._current_task_id = None
    worker._active_task_pids = {}
    return worker


async def test_orphan_redirect_becomes_a_queued_task(
    db_session: AsyncSession,
    test_settings,
    mock_queue,
    mock_redis,
) -> None:
    user_id, session_id = await _user_and_session(
        db_session, test_settings, str(test_settings.projects_root)
    )
    running = await TaskRepository(db_session).create(
        user_id=user_id,
        task_type="agent_prompt",
        payload=json.dumps({"prompt": "dead turn"}),
        session_id=session_id,
    )
    await TaskRepository(db_session).mark_running(running.id)
    await db_session.commit()
    exec_svc = SessionExecutionService(mock_redis)
    await exec_svc.set_pending_redirect(
        session_id,
        {
            "prompt": "new instruction",
            "workspace": str(test_settings.projects_root),
            "agent_workspace": str(test_settings.projects_root),
        },
    )
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    worker = _worker(test_settings, session_factory, mock_redis, mock_queue)

    await worker._promote_orphan_redirect(str(session_id))

    mock_queue.enqueue.assert_awaited_once()
    async with session_factory() as check:
        repo = TaskRepository(check)
        stale = await repo.get_by_id(running.id)
        assert stale is not None
        assert stale.status == "failed"
        pending = await repo.list_pending_agent_for_session(session_id)
        assert len(pending) == 1
        payload = json.loads(pending[0].payload or "{}")
        assert payload["prompt"] == "new instruction"
    assert await exec_svc.peek_pending_redirect(session_id) is None


async def test_startup_drains_redirect_left_during_restart(
    db_session: AsyncSession,
    test_settings,
    mock_queue,
    mock_redis,
) -> None:
    user_id, session_id = await _user_and_session(
        db_session, test_settings, str(test_settings.projects_root)
    )
    await db_session.commit()
    await SessionExecutionService(mock_redis).set_pending_redirect(
        session_id,
        {"prompt": "hello after restart", "workspace": str(test_settings.projects_root)},
    )
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    worker = _worker(test_settings, session_factory, mock_redis, mock_queue)

    await worker._drain_orphan_redirects()

    mock_queue.enqueue.assert_awaited_once()


async def test_release_if_owner_does_not_clobber_replacement(
    mock_redis,
) -> None:
    session_id = uuid.uuid4()
    exec_svc = SessionExecutionService(mock_redis)
    await exec_svc.register_running_task(session_id, uuid.uuid4())
    await exec_svc.release_if_owner(session_id, pid=424242)
    assert await exec_svc.get_running_task_id(session_id) is not None
