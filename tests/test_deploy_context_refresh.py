"""Deploy context refresh from DB."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.repositories.session import SessionRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.services.deploy_context_snapshot import (
    ensure_marker,
    refresh_deploy_context_from_db,
)
from telegram_cursor_agent.services.deploy_resume import DeployContext, read_marker


@pytest.mark.asyncio
async def test_refresh_deploy_context_fills_cursor_chat_id_from_db(
    db_session: AsyncSession, test_settings, tmp_path
) -> None:
    users = UserRepository(db_session)
    sessions = SessionRepository(db_session)
    user = await users.upsert(telegram_id=9001, username="u", is_admin=True)
    agent_session = await sessions.create(
        user_id=user.id,
        workspace_path=str(tmp_path),
        cursor_chat_id="chat-from-db",
    )
    await db_session.commit()

    stale = DeployContext(
        session_id=agent_session.id,
        user_id=user.id,
        cursor_chat_id=None,
        workspace="/wrong",
    )

    from sqlalchemy.ext.asyncio import async_sessionmaker

    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    refreshed = await refresh_deploy_context_from_db(
        session_factory, stale, test_settings
    )
    assert refreshed.cursor_chat_id == "chat-from-db"
    assert refreshed.workspace == str(tmp_path)


def test_ensure_marker_patches_existing_marker(
    test_settings, tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(
        test_settings, "self_repo_root", tmp_path, raising=False
    )
    write_context = DeployContext(
        telegram_id=1,
        session_id=uuid.uuid4(),
        cursor_chat_id="old",
        workspace="/a",
    )
    from telegram_cursor_agent.services.deploy_resume import write_marker

    write_marker(test_settings, write_context, status="session_active")
    ensure_marker(
        test_settings,
        DeployContext(
            telegram_id=1,
            session_id=write_context.session_id,
            cursor_chat_id="fresh-from-snapshot",
            workspace="/b",
        ),
    )
    marker = read_marker(test_settings)
    assert marker is not None
    assert marker.cursor_chat_id == "fresh-from-snapshot"
    assert marker.workspace == "/b"
