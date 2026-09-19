"""Session service tests."""

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.sessions import SessionError, SessionService
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.repositories.user import UserRepository


async def _create_user(db: AsyncSession) -> uuid.UUID:
    users = UserRepository(db)
    user = await users.upsert(telegram_id=12345, username="tester", is_admin=False)
    return user.id


@pytest.mark.asyncio
async def test_create_new_archives_previous(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    user_id = await _create_user(db_session)
    service = SessionService(db_session, test_settings)

    first = await service.get_or_create_active(user_id, str(test_settings.projects_root))
    second = await service.create_new(user_id, str(test_settings.projects_root))

    await db_session.refresh(first)
    assert first.status == "archived"
    assert second.status == "active"
    assert second.cursor_chat_id is None
    assert second.id != first.id


@pytest.mark.asyncio
async def test_activate_session(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    user_id = await _create_user(db_session)
    service = SessionService(db_session, test_settings)

    first = await service.get_or_create_active(user_id, str(test_settings.projects_root))
    await service._sessions.archive_all_for_user(user_id)
    second = await service._sessions.create(
        user_id=user_id,
        workspace_path=str(test_settings.projects_root),
        cursor_chat_id="chat-2",
    )
    second.status = "archived"
    await db_session.flush()

    activated = await service.activate(user_id, second.id)
    await db_session.refresh(first)

    assert activated.id == second.id
    assert activated.status == "active"
    assert first.status == "archived"


@pytest.mark.asyncio
async def test_delete_session(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    user_id = await _create_user(db_session)
    service = SessionService(db_session, test_settings)
    active = await service.get_or_create_active(user_id, str(test_settings.projects_root))

    deleted = await service.delete(user_id, active.id)
    assert deleted.status == "deleted"
    assert await service.get_active(user_id) is None


@pytest.mark.asyncio
async def test_resolve_selector_by_index(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    user_id = await _create_user(db_session)
    service = SessionService(db_session, test_settings)
    first = await service.get_or_create_active(user_id, str(test_settings.projects_root))
    await service._sessions.archive_all_for_user(user_id)
    second = await service._sessions.create(
        user_id=user_id,
        workspace_path=str(test_settings.projects_root),
        cursor_chat_id="chat-2",
    )

    sessions = await service.list_resumable(user_id)
    index = sessions.index(second) + 1
    resolved = await service.resolve_selector(user_id, str(index))
    assert resolved.cursor_chat_id == "chat-2"
    assert resolved.id == second.id
    assert first.status == "archived"


@pytest.mark.asyncio
async def test_set_title_if_empty(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    user_id = await _create_user(db_session)
    service = SessionService(db_session, test_settings)
    active = await service.get_or_create_active(user_id, str(test_settings.projects_root))

    updated = await service.set_title_if_empty(
        active.id,
        "добавь /limits",
        "Команда /limits показывает лимиты Cursor.",
    )
    assert updated is not None
    assert updated.title
    assert len(updated.title) <= 64

    unchanged = await service.set_title_if_empty(
        active.id,
        "другой запрос",
        "другой ответ",
    )
    assert unchanged is not None
    assert unchanged.title == updated.title


@pytest.mark.asyncio
async def test_resolve_selector_not_found(
    db_session: AsyncSession, test_settings: Settings
) -> None:
    user_id = await _create_user(db_session)
    service = SessionService(db_session, test_settings)
    with pytest.raises(SessionError, match="Нет сохранённых сессий"):
        await service.resolve_selector(user_id, "1")
