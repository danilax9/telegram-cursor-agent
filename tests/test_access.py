"""Access management tests."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.services.access import AccessError, AccessService


async def test_grant_and_list(db_session: AsyncSession, test_settings) -> None:
    owners = UserRepository(db_session)
    await owners.upsert(12345, "owner", is_admin=True)
    service = AccessService(db_session, test_settings)

    result = await service.grant(12345, "999001")
    assert "999001" in result

    users = await owners.list_authorized()
    ids = {user.telegram_id for user in users}
    assert 999001 in ids


async def test_grant_by_username_requires_known_user(
    db_session: AsyncSession, test_settings
) -> None:
    owners = UserRepository(db_session)
    await owners.upsert(12345, "owner", is_admin=True)
    await owners.upsert(777001, "friend", is_admin=False)
    service = AccessService(db_session, test_settings)

    result = await service.grant(12345, "@friend")
    assert "@friend" in result

    user = await owners.get_by_telegram_id(777001)
    assert user is not None
    assert user.is_admin


async def test_non_owner_cannot_grant(db_session: AsyncSession, test_settings) -> None:
    users = UserRepository(db_session)
    await users.upsert(55555, "guest", is_admin=True)
    service = AccessService(db_session, test_settings)

    with pytest.raises(PermissionError):
        await service.grant(55555, "999002")


async def test_revoke_owner_forbidden(db_session: AsyncSession, test_settings) -> None:
    users = UserRepository(db_session)
    await users.upsert(12345, "owner", is_admin=True)
    service = AccessService(db_session, test_settings)

    with pytest.raises(AccessError, match="владельца"):
        await service.revoke(12345, "12345")


async def test_start_does_not_revoke_granted_access(
    db_session: AsyncSession, test_settings
) -> None:
    users = UserRepository(db_session)
    await users.grant_access(888001, "granted")
    await users.upsert(888001, "granted", is_admin=None)
    user = await users.get_by_telegram_id(888001)
    assert user is not None
    assert user.is_admin
