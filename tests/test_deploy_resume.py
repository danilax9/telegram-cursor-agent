"""Deploy resume marker and recovery tests."""

import json
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram_cursor_agent.database.repositories.session import SessionRepository
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.services.deploy_recovery import (
    DeployRecoveryService,
    _dedupe_notifications,
    _ensure_online_notification,
)
from telegram_cursor_agent.services.deploy_resume import (
    DEPLOY_PENDING_STATUS,
    DEPLOY_SUCCESS_MESSAGE,
    DeployContext,
    claim_deploy_start_notification,
    clear_marker,
    marker_path,
    read_marker,
    write_marker,
)


@pytest.fixture
def deploy_settings(test_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("SELF_DEPLOY_ENABLED", "true")
    monkeypatch.setenv("SELF_REPO_ROOT", str(tmp_path))
    from telegram_cursor_agent.core.config import clear_settings_cache, get_settings

    clear_settings_cache()
    return get_settings()


def test_ensure_online_notification_is_single_per_user() -> None:
    tid = 42
    once = _ensure_online_notification([], tid)
    twice = _ensure_online_notification(once, tid)
    assert twice == once
    deduped = _dedupe_notifications(
        once
        + [{"telegram_id": tid, "message": DEPLOY_SUCCESS_MESSAGE}]
    )
    assert len(deduped) == 1
    assert deduped[0]["message"] == DEPLOY_SUCCESS_MESSAGE


async def test_marker_roundtrip(deploy_settings) -> None:
    context = DeployContext(
        task_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        telegram_id=42,
        session_id=uuid.uuid4(),
        cursor_chat_id="chat-123",
        workspace="/tmp/workspace",
    )
    write_marker(deploy_settings, context, status="started")
    marker = read_marker(deploy_settings)
    assert marker is not None
    assert marker.status == "started"
    assert marker.cursor_chat_id == "chat-123"
    assert marker.telegram_id == 42
    clear_marker(deploy_settings)
    assert read_marker(deploy_settings) is None


def test_deploy_start_warning_is_sent_only_once(deploy_settings) -> None:
    context = DeployContext(telegram_id=6780844442)
    write_marker(deploy_settings, context, status="started")

    first = claim_deploy_start_notification(deploy_settings)
    second = claim_deploy_start_notification(deploy_settings)

    assert first == 6780844442
    assert second is None
    marker = read_marker(deploy_settings)
    assert marker is not None
    assert marker.deploy_notified is True
    assert marker.status == DEPLOY_PENDING_STATUS


async def test_stored_notifications_always_send_online_after_deploy(
    db_session, deploy_settings, tmp_path
) -> None:
    """DB completed does not mean Telegram got the online message — always send."""
    users = UserRepository(db_session)
    tasks = TaskRepository(db_session)
    user = await users.upsert(telegram_id=2003, username="admin", is_admin=True)
    deploy_task = await tasks.create(
        user_id=user.id,
        task_type="deploy",
        payload="{}",
    )
    await tasks.mark_completed(deploy_task.id, "🟢 Бот онлайн после перезапуска")
    await db_session.commit()

    marker_path(deploy_settings).write_text(
        json.dumps(
            {
                "version": 1,
                "status": "pending_resume",
                "task_id": str(deploy_task.id),
                "telegram_id": user.telegram_id,
                "tasks_recovered": True,
                "notifications": [],
            }
        )
    )

    notifier = MagicMock()
    notifier.send = AsyncMock()
    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        deploy_settings, session_factory, notifier=notifier, redis=MagicMock()
    )
    await recovery.deliver_stored_notifications()

    notifier.send.assert_awaited_once_with(
        user.telegram_id, DEPLOY_SUCCESS_MESSAGE
    )


async def test_worker_recovery_stores_notifications_without_sending(
    db_session, deploy_settings, tmp_path
) -> None:
    users = UserRepository(db_session)
    sessions = SessionRepository(db_session)
    tasks = TaskRepository(db_session)

    user = await users.upsert(telegram_id=999, username="admin", is_admin=True)
    agent_session = await sessions.create(
        user_id=user.id,
        workspace_path=str(tmp_path),
        cursor_chat_id="cursor-chat-1",
    )
    deploy_task = await tasks.create(
        user_id=user.id,
        task_type="deploy",
        payload="{}",
        session_id=agent_session.id,
    )
    await tasks.mark_running(deploy_task.id)
    context = DeployContext(
        task_id=deploy_task.id,
        user_id=user.id,
        telegram_id=user.telegram_id,
        session_id=agent_session.id,
        cursor_chat_id="cursor-chat-1",
        workspace=str(tmp_path),
    )
    write_marker(deploy_settings, context, status="started")
    marker_path(deploy_settings).write_text(
        json.dumps(
            {
                "version": 1,
                "status": "pending_resume",
                "task_id": str(context.task_id),
                "user_id": str(context.user_id),
                "telegram_id": context.telegram_id,
                "session_id": str(context.session_id),
                "cursor_chat_id": context.cursor_chat_id,
                "workspace": context.workspace,
                "deploy_log": "deployed ok",
                "created_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:01+00:00",
            }
        )
    )

    await db_session.commit()

    notifier = MagicMock()
    notifier.send = AsyncMock()

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(
        bind=db_session.bind, expire_on_commit=False
    )

    recovery = DeployRecoveryService(deploy_settings, session_factory, notifier)
    await recovery.recover_tasks_on_startup()

    notifier.send.assert_not_awaited()
    marker = read_marker(deploy_settings)
    assert marker is not None
    assert marker.tasks_recovered is True
    assert marker.notifications
    assert marker.notifications[0]["telegram_id"] == user.telegram_id


async def test_recovery_stores_online_notification_for_completed_deploy_task(
    db_session, deploy_settings, tmp_path
) -> None:
    users = UserRepository(db_session)
    tasks = TaskRepository(db_session)

    user = await users.upsert(telegram_id=1001, username="admin", is_admin=True)
    task = await tasks.create(user_id=user.id, task_type="deploy", payload="{}")
    await tasks.mark_completed(task.id, "already done")

    marker_path(deploy_settings).write_text(
        json.dumps(
            {
                "version": 1,
                "status": "pending_resume",
                "task_id": str(task.id),
                "user_id": str(user.id),
                "telegram_id": user.telegram_id,
                "created_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:01+00:00",
            }
        )
    )

    await db_session.commit()

    notifier = MagicMock()
    notifier.send = AsyncMock()

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(
        bind=db_session.bind, expire_on_commit=False
    )

    recovery = DeployRecoveryService(deploy_settings, session_factory, notifier)
    await recovery.recover_tasks_on_startup()

    notifier.send.assert_not_awaited()
    marker = read_marker(deploy_settings)
    assert marker is not None
    assert marker.tasks_recovered is True
    assert any(
        item.get("message") == DEPLOY_SUCCESS_MESSAGE
        for item in (marker.notifications or [])
    )


async def test_recovery_queues_deploy_resume_task(
    db_session, deploy_settings, tmp_path, monkeypatch
) -> None:
    users = UserRepository(db_session)
    sessions = SessionRepository(db_session)
    tasks = TaskRepository(db_session)

    user = await users.upsert(telegram_id=3003, username="admin", is_admin=True)
    agent_session = await sessions.create(
        user_id=user.id,
        workspace_path=str(tmp_path),
        cursor_chat_id="cursor-chat-resume",
    )
    deploy_task = await tasks.create(
        user_id=user.id,
        task_type="deploy",
        payload="{}",
        session_id=agent_session.id,
    )
    await tasks.mark_running(deploy_task.id)
    await db_session.commit()

    marker_path(deploy_settings).write_text(
        json.dumps(
            {
                "version": 1,
                "status": "pending_resume",
                "task_id": str(deploy_task.id),
                "user_id": str(user.id),
                "telegram_id": user.telegram_id,
                "session_id": str(agent_session.id),
                "cursor_chat_id": "cursor-chat-resume",
                "workspace": str(tmp_path),
                "created_at": "2026-01-01T00:00:00+00:00",
                "completed_at": "2026-01-01T00:00:01+00:00",
            }
        )
    )

    redis = MagicMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    redis.rpush = AsyncMock()
    redis.publish = AsyncMock()

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        deploy_settings, session_factory, redis=redis
    )
    await recovery.recover_tasks_on_startup()

    pending = await tasks.list_pending(limit=10)
    assert len(pending) == 1
    assert pending[0].task_type == "agent_prompt"
    payload = json.loads(pending[0].payload or "{}")
    assert payload.get("deploy_recovery") is True
    redis.rpush.assert_awaited()


async def test_unexpected_restart_notifies_and_resumes(
    db_session, deploy_settings, tmp_path
) -> None:
    """Manual/crash restart must notify the user (not only formal /deploy)."""
    users = UserRepository(db_session)
    sessions = SessionRepository(db_session)
    tasks = TaskRepository(db_session)

    user = await users.upsert(telegram_id=5555, username="admin", is_admin=True)
    agent_session = await sessions.create(
        user_id=user.id,
        workspace_path=str(tmp_path),
        cursor_chat_id="cursor-chat-interrupt",
    )
    running = await tasks.create(
        user_id=user.id,
        task_type="agent_prompt",
        payload=json.dumps({"prompt": "do something", "agent_workspace": str(tmp_path)}),
        session_id=agent_session.id,
    )
    await tasks.mark_running(running.id)
    await db_session.commit()

    write_marker(
        deploy_settings,
        DeployContext(
            task_id=running.id,
            user_id=user.id,
            telegram_id=user.telegram_id,
            session_id=agent_session.id,
            cursor_chat_id="cursor-chat-interrupt",
            workspace=str(tmp_path),
        ),
        status="session_active",
    )

    notifier = MagicMock()
    notifier.send = AsyncMock()
    redis = MagicMock()
    redis.set = AsyncMock()
    redis.get = AsyncMock(return_value=None)
    redis.delete = AsyncMock()
    redis.rpush = AsyncMock()
    redis.publish = AsyncMock()

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        deploy_settings, session_factory, notifier=notifier, redis=redis
    )
    await recovery.recover_tasks_on_startup()

    notifier.send.assert_awaited()
    sent_text = notifier.send.await_args.args[1]
    assert "перезапустился" in sent_text.lower() or "перезапуск" in sent_text.lower()

    async with session_factory() as check_db:
        updated = await TaskRepository(check_db).get_by_id(running.id)
        assert updated is not None
        assert updated.status == "failed"
        pending = await TaskRepository(check_db).list_pending(limit=10)
        assert len(pending) == 1
    payload = json.loads(pending[0].payload or "{}")
    assert payload.get("interrupt_recovery") is True
    assert "do something" in payload.get("prompt", "")
    assert read_marker(deploy_settings) is None


async def test_bot_skips_notify_for_session_active_marker(
    db_session, deploy_settings, monkeypatch
) -> None:
    marker_path(deploy_settings).write_text(
        json.dumps(
            {
                "version": 1,
                "status": "session_active",
                "telegram_id": 42,
                "tasks_recovered": False,
            }
        )
    )
    monkeypatch.setenv("DEPLOY_RECOVERY_DELAY_SECONDS", "0")
    from telegram_cursor_agent.core.config import clear_settings_cache, get_settings

    clear_settings_cache()
    settings = get_settings()

    notifier = MagicMock()
    notifier.send = AsyncMock()
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    recovery = DeployRecoveryService(
        settings, session_factory, notifier=notifier, redis=redis
    )
    await recovery.deliver_notifications_when_ready()

    notifier.send.assert_not_awaited()
    # Stale marker left for worker to clear; bot must not hang waiting.
    assert read_marker(settings) is not None


async def test_bot_delivers_notifications_when_ready(
    db_session, deploy_settings, tmp_path, monkeypatch
) -> None:
    users = UserRepository(db_session)
    user = await users.upsert(telegram_id=2002, username="admin", is_admin=True)
    await db_session.commit()

    marker_path(deploy_settings).write_text(
        json.dumps(
            {
                "version": 1,
                "status": "pending_resume",
                "telegram_id": user.telegram_id,
                "recovery_token": "token-123",
                "tasks_recovered": True,
                "notifications": [
                    {
                        "telegram_id": user.telegram_id,
                        "message": "🟢 Бот онлайн после перезапуска",
                    }
                ],
            }
        )
    )

    monkeypatch.setenv("DEPLOY_RECOVERY_DELAY_SECONDS", "0")
    from telegram_cursor_agent.core.config import clear_settings_cache, get_settings

    clear_settings_cache()
    settings = get_settings()

    notifier = MagicMock()
    notifier.send = AsyncMock()
    redis = MagicMock()
    redis.get = AsyncMock(return_value=b"token-123")

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(
        bind=db_session.bind, expire_on_commit=False
    )

    recovery = DeployRecoveryService(
        settings, session_factory, notifier=notifier, redis=redis
    )
    await recovery.deliver_notifications_when_ready()

    notifier.send.assert_awaited_once_with(
        user.telegram_id, "🟢 Бот онлайн после перезапуска"
    )
    assert read_marker(settings) is None


def test_fix_request_is_claimed_once(deploy_settings) -> None:
    from telegram_cursor_agent.services.deploy_resume import (
        build_rollback_diagnosis_prompt,
        claim_fix_request,
        write_fix_request,
    )

    write_fix_request(
        deploy_settings,
        reason="bot crashed while typing",
        telegram_id=1,
        user_id="u",
        session_id="s",
        cursor_chat_id="chat",
        workspace="/tmp",
    )
    first = claim_fix_request(deploy_settings)
    second = claim_fix_request(deploy_settings)
    assert first is not None
    assert first["cursor_chat_id"] == "chat"
    assert second is None
    prompt = build_rollback_diagnosis_prompt("bot crashed while typing")
    assert "последн" in prompt.lower() or "last dialogue" in prompt.lower()
    assert "bot crashed while typing" in prompt
