"""Deploy context snapshot tests."""

import json
import uuid

import pytest

from telegram_cursor_agent.database.repositories.session import SessionRepository
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.services.deploy_context_snapshot import (
    ensure_marker,
    snapshot_running_deploy_context,
)
from telegram_cursor_agent.services.deploy_resume import read_marker


@pytest.fixture
def deploy_settings(test_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("SELF_DEPLOY_ENABLED", "true")
    monkeypatch.setenv("SELF_REPO_ROOT", str(tmp_path))
    from telegram_cursor_agent.core.config import clear_settings_cache, get_settings

    clear_settings_cache()
    return get_settings()


async def test_snapshot_running_agent_prompt(db_session, deploy_settings, tmp_path) -> None:
    users = UserRepository(db_session)
    sessions = SessionRepository(db_session)
    tasks = TaskRepository(db_session)

    user = await users.upsert(telegram_id=4242, username="admin", is_admin=True)
    agent_session = await sessions.create(
        user_id=user.id,
        workspace_path=str(tmp_path),
        cursor_chat_id="cursor-chat-42",
    )
    task = await tasks.create(
        user_id=user.id,
        task_type="agent_prompt",
        payload=json.dumps({"prompt": "fix bug"}),
        session_id=agent_session.id,
    )
    await tasks.mark_running(task.id)
    await db_session.commit()

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    context = await snapshot_running_deploy_context(session_factory)

    assert context is not None
    assert context.telegram_id == 4242
    assert context.cursor_chat_id == "cursor-chat-42"
    assert context.task_id == task.id


async def test_ensure_marker_writes_context(deploy_settings, tmp_path) -> None:
    from telegram_cursor_agent.services.deploy_resume import DeployContext

    context = DeployContext(
        task_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        telegram_id=111,
        session_id=uuid.uuid4(),
        cursor_chat_id="chat-1",
        workspace=str(tmp_path),
    )
    ensure_marker(deploy_settings, context)
    marker = read_marker(deploy_settings)
    assert marker is not None
    assert marker.telegram_id == 111
    assert marker.cursor_chat_id == "chat-1"
