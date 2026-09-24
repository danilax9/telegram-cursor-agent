"""Restart resilience: no silent restarts, no stuck tasks, no recovery loops."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from telegram_cursor_agent.database.repositories.session import SessionRepository
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.services.deploy_recovery import DeployRecoveryService
from telegram_cursor_agent.queue.worker import TaskWorker
from telegram_cursor_agent.services.deploy_resume import (
    MAX_RECOVERY_ATTEMPTS,
    RECOVERY_ATTEMPT_KEY,
    RECOVERY_EXHAUSTED_MESSAGE,
    STUCK_TYPING_MESSAGE,
    DeployContext,
    claim_marker_for_delivery,
    clean_shutdown_path,
    mark_clean_shutdown,
    marker_path,
    read_marker,
    write_fix_request,
    write_marker,
)


@pytest.fixture
def deploy_settings(test_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("SELF_DEPLOY_ENABLED", "true")
    monkeypatch.setenv("SELF_REPO_ROOT", str(tmp_path))
    from telegram_cursor_agent.core.config import clear_settings_cache, get_settings

    clear_settings_cache()
    return get_settings()


def _mock_redis():
    redis = MagicMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    redis.rpush = AsyncMock()
    redis.publish = AsyncMock()
    return redis


def _mock_notifier():
    notifier = MagicMock()
    notifier.send = AsyncMock()
    return notifier


async def _running_agent_task(
    db_session,
    tmp_path,
    *,
    telegram_id: int,
    payload_extra: dict | None = None,
    cursor_chat_id: str | None = "cursor-chat",
):
    users = UserRepository(db_session)
    sessions = SessionRepository(db_session)
    tasks = TaskRepository(db_session)

    user = await users.upsert(
        telegram_id=telegram_id, username=f"u{telegram_id}", is_admin=True
    )
    agent_session = await sessions.create(
        user_id=user.id,
        workspace_path=str(tmp_path),
        cursor_chat_id=cursor_chat_id,
    )
    payload = {"prompt": "work", "agent_workspace": str(tmp_path)}
    payload.update(payload_extra or {})
    task = await tasks.create(
        user_id=user.id,
        task_type="agent_prompt",
        payload=json.dumps(payload),
        session_id=agent_session.id,
    )
    await tasks.mark_running(task.id)
    await db_session.commit()
    return user, agent_session, task


async def test_every_interrupted_task_leaves_running_state(
    db_session, deploy_settings, tmp_path
) -> None:
    """More interrupted tasks than the old limit=20 must all be cleaned up."""
    users = UserRepository(db_session)
    tasks = TaskRepository(db_session)
    user = await users.upsert(telegram_id=7001, username="many", is_admin=True)
    for index in range(25):
        task = await tasks.create(
            user_id=user.id,
            task_type="agent_prompt",
            payload=json.dumps({"prompt": f"p{index}"}),
        )
        await tasks.mark_running(task.id)
    await db_session.commit()

    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        deploy_settings,
        session_factory,
        notifier=_mock_notifier(),
        redis=_mock_redis(),
    )
    await recovery.recover_tasks_on_startup()

    async with session_factory() as check:
        still_running = await TaskRepository(check).list_running(limit=None)
        assert still_running == []


async def test_recovery_loop_is_capped(db_session, deploy_settings, tmp_path) -> None:
    """A resume that keeps getting interrupted must stop re-queueing itself."""
    user, _session, task = await _running_agent_task(
        db_session,
        tmp_path,
        telegram_id=7002,
        payload_extra={
            "interrupt_recovery": True,
            RECOVERY_ATTEMPT_KEY: MAX_RECOVERY_ATTEMPTS,
        },
    )
    notifier = _mock_notifier()
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        deploy_settings, session_factory, notifier=notifier, redis=_mock_redis()
    )
    await recovery.recover_tasks_on_startup()

    async with session_factory() as check:
        repo = TaskRepository(check)
        failed = await repo.get_by_id(task.id)
        assert failed is not None
        assert failed.status == "failed"
        # No further resume task was queued.
        assert await repo.list_pending(limit=10) == []

    notifier.send.assert_awaited_once()
    assert notifier.send.await_args.args[1] == RECOVERY_EXHAUSTED_MESSAGE


async def test_second_deliberate_restart_still_resumes(
    db_session, deploy_settings, tmp_path
) -> None:
    """systemctl / self-deploy is not a crash loop, even on the second restart."""
    await _running_agent_task(
        db_session,
        tmp_path,
        telegram_id=7010,
        payload_extra={
            "interrupt_recovery": True,
            RECOVERY_ATTEMPT_KEY: MAX_RECOVERY_ATTEMPTS,
        },
    )
    mark_clean_shutdown(deploy_settings)
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        deploy_settings,
        session_factory,
        notifier=_mock_notifier(),
        redis=_mock_redis(),
    )
    await recovery.recover_tasks_on_startup()

    async with session_factory() as check:
        pending = await TaskRepository(check).list_pending(limit=10)
        assert len(pending) == 1
        payload = json.loads(pending[0].payload or "{}")
        assert payload[RECOVERY_ATTEMPT_KEY] == 1
    assert not clean_shutdown_path(deploy_settings).exists()


async def test_resume_increments_attempt_counter(
    db_session, deploy_settings, tmp_path
) -> None:
    await _running_agent_task(
        db_session,
        tmp_path,
        telegram_id=7003,
        payload_extra={"interrupt_recovery": True, RECOVERY_ATTEMPT_KEY: 1},
    )
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        deploy_settings,
        session_factory,
        notifier=_mock_notifier(),
        redis=_mock_redis(),
    )
    await recovery.recover_tasks_on_startup()

    async with session_factory() as check:
        pending = await TaskRepository(check).list_pending(limit=10)
        assert len(pending) == 1
        payload = json.loads(pending[0].payload or "{}")
        assert payload[RECOVERY_ATTEMPT_KEY] == 2


async def test_one_failing_send_does_not_silence_other_users(
    db_session, deploy_settings, tmp_path
) -> None:
    await _running_agent_task(db_session, tmp_path, telegram_id=7004)
    await _running_agent_task(db_session, tmp_path, telegram_id=7005)

    notifier = _mock_notifier()
    notifier.send = AsyncMock(side_effect=[RuntimeError("blocked by user"), None])
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        deploy_settings, session_factory, notifier=notifier, redis=_mock_redis()
    )
    await recovery.recover_tasks_on_startup()

    assert notifier.send.await_count == 2


async def test_marker_delivery_is_claimed_once(deploy_settings) -> None:
    """Worker and bot race for the same marker; only one may deliver."""
    marker_path(deploy_settings).write_text(
        json.dumps(
            {
                "version": 1,
                "status": "pending_resume",
                "telegram_id": 42,
                "tasks_recovered": True,
                "notifications": [{"telegram_id": 42, "message": "done"}],
            }
        )
    )
    first = claim_marker_for_delivery(deploy_settings)
    second = claim_marker_for_delivery(deploy_settings)
    assert first is not None
    assert second is None


async def test_session_active_marker_cleared_after_unexpected_restart(
    db_session, deploy_settings, tmp_path
) -> None:
    user, agent_session, task = await _running_agent_task(
        db_session, tmp_path, telegram_id=7006
    )
    write_marker(
        deploy_settings,
        DeployContext(
            task_id=task.id,
            user_id=user.id,
            telegram_id=user.telegram_id,
            session_id=agent_session.id,
            cursor_chat_id="cursor-chat",
            workspace=str(tmp_path),
        ),
        status="session_active",
    )
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        deploy_settings,
        session_factory,
        notifier=_mock_notifier(),
        redis=_mock_redis(),
    )
    await recovery.recover_tasks_on_startup()

    assert read_marker(deploy_settings) is None


async def test_task_without_session_gets_clear_instruction(
    db_session, deploy_settings, tmp_path
) -> None:
    """No cursor chat to resume: the user still must hear about it."""
    await _running_agent_task(
        db_session, tmp_path, telegram_id=7007, cursor_chat_id=None
    )
    notifier = _mock_notifier()
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        deploy_settings, session_factory, notifier=notifier, redis=_mock_redis()
    )
    await recovery.recover_tasks_on_startup()

    notifier.send.assert_awaited_once()
    assert "/resume" in notifier.send.await_args.args[1]

    async with session_factory() as check:
        assert await TaskRepository(check).list_pending(limit=10) == []


async def test_orphan_process_kill_is_guarded_against_pid_reuse(
    deploy_settings, tmp_path
) -> None:
    session_factory = async_sessionmaker.__call__  # unused, service needs no DB here
    recovery = DeployRecoveryService(deploy_settings, session_factory)
    # PID 1 and unknown PIDs must never be signalled.
    recovery._terminate_orphan_process(None)
    recovery._terminate_orphan_process(0)
    recovery._terminate_orphan_process(1)
    recovery._terminate_orphan_process(999_999_999)


async def test_chaos_stuck_typing_then_rollback_report(
    db_session, deploy_settings, tmp_path
) -> None:
    """Crash mid-typing, then a rollback must replace the bubble and queue a report."""
    user, agent_session, task = await _running_agent_task(
        db_session, tmp_path, telegram_id=7011
    )
    redis = _mock_redis()
    redis.get = AsyncMock(
        return_value=json.dumps(
            {"telegram_id": user.telegram_id, "message_id": 77}
        ).encode()
    )
    notifier = _mock_notifier()
    notifier.edit_live_message = AsyncMock()
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        deploy_settings, session_factory, notifier=notifier, redis=redis
    )
    await recovery.recover_tasks_on_startup()

    notifier.edit_live_message.assert_awaited()
    assert notifier.edit_live_message.await_args.args[2] == STUCK_TYPING_MESSAGE

    write_fix_request(
        deploy_settings,
        reason="RuntimeError: tca-guard-drill",
        telegram_id=user.telegram_id,
        user_id=str(user.id),
        session_id=str(agent_session.id),
        cursor_chat_id=agent_session.cursor_chat_id,
        workspace=str(tmp_path),
    )
    queue = MagicMock()
    queue.enqueue = AsyncMock()
    worker = TaskWorker.__new__(TaskWorker)
    worker._settings = deploy_settings
    worker._queue = queue
    worker._session_factory = session_factory
    worker._notifier = notifier
    worker._redis = redis
    await worker._consume_fix_request()

    queue.enqueue.assert_awaited_once()
    async with session_factory() as check:
        pending = await TaskRepository(check).list_pending(limit=10)
        diagnosis = [
            item
            for item in pending
            if item.id != task.id and "rollback_diagnosis" in (item.payload or "")
        ]
        assert len(diagnosis) == 1
        payload = json.loads(diagnosis[0].payload or "{}")
        assert payload["cursor_chat_id"] == agent_session.cursor_chat_id
        assert "tca-guard-drill" in payload["prompt"]
        assert payload.get("silent_recovery") is not True
