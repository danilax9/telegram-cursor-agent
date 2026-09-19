"""Confirmation state machine tests."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.repositories.confirmation import ConfirmationRepository
from telegram_cursor_agent.database.repositories.user import UserRepository


async def _create_user(db: AsyncSession) -> uuid.UUID:
    users = UserRepository(db)
    user = await users.upsert(telegram_id=12345, username="admin", is_admin=True)
    return user.id


async def test_create_and_approve(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = ConfirmationRepository(db_session)

    confirmation = await repo.create(
        user_id=user_id,
        action_type="run_command",
        action_payload='{"command": "git push"}',
        ttl_seconds=300,
    )
    assert confirmation.status == "pending"

    approved = await repo.approve(confirmation.id)
    assert approved is not None
    assert approved.status == "approved"
    assert approved.resolved_at is not None


async def test_reject(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = ConfirmationRepository(db_session)

    confirmation = await repo.create(
        user_id=user_id,
        action_type="run_command",
        action_payload="{}",
        ttl_seconds=300,
    )
    rejected = await repo.reject(confirmation.id)
    assert rejected is not None
    assert rejected.status == "rejected"


async def test_expired_confirmation(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = ConfirmationRepository(db_session)

    confirmation = await repo.create(
        user_id=user_id,
        action_type="run_command",
        action_payload="{}",
        ttl_seconds=-1,
    )
    confirmation.expires_at = datetime.now(UTC) - timedelta(seconds=10)
    await db_session.flush()

    approved = await repo.approve(confirmation.id)
    assert approved is None

    refreshed = await repo.get_by_id(confirmation.id)
    assert refreshed is not None
    assert refreshed.status == "expired"


async def test_expire_stale(db_session: AsyncSession) -> None:
    user_id = await _create_user(db_session)
    repo = ConfirmationRepository(db_session)

    confirmation = await repo.create(
        user_id=user_id,
        action_type="run_command",
        action_payload="{}",
        ttl_seconds=-10,
    )
    confirmation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.flush()

    count = await repo.expire_stale()
    assert count >= 1
