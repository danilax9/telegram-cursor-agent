"""Confirmation repository with expiring state machine."""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.models.confirmation import Confirmation


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class ConfirmationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        user_id: uuid.UUID,
        action_type: str,
        action_payload: str,
        ttl_seconds: int,
        telegram_message_id: int | None = None,
    ) -> Confirmation:
        confirmation = Confirmation(
            user_id=user_id,
            action_type=action_type,
            action_payload=action_payload,
            status="pending",
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl_seconds),
            telegram_message_id=telegram_message_id,
        )
        self._session.add(confirmation)
        await self._session.flush()
        return confirmation

    async def get_by_id(self, confirmation_id: uuid.UUID) -> Confirmation | None:
        result = await self._session.execute(
            select(Confirmation).where(Confirmation.id == confirmation_id)
        )
        return result.scalar_one_or_none()

    async def get_pending_for_user(self, user_id: uuid.UUID) -> Confirmation | None:
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(Confirmation)
            .where(
                Confirmation.user_id == user_id,
                Confirmation.status == "pending",
                Confirmation.expires_at > now,
            )
            .order_by(Confirmation.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_pending_by_type(
        self, user_id: uuid.UUID, action_type: str
    ) -> Confirmation | None:
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(Confirmation)
            .where(
                Confirmation.user_id == user_id,
                Confirmation.action_type == action_type,
                Confirmation.status == "pending",
                Confirmation.expires_at > now,
            )
            .order_by(Confirmation.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def approve(self, confirmation_id: uuid.UUID) -> Confirmation | None:
        confirmation = await self.get_by_id(confirmation_id)
        if confirmation is None or confirmation.status != "pending":
            return None
        if _as_utc(confirmation.expires_at) < datetime.now(UTC):
            confirmation.status = "expired"
            await self._session.flush()
            return None
        confirmation.status = "approved"
        confirmation.resolved_at = datetime.now(UTC)
        await self._session.flush()
        return confirmation

    async def reject(self, confirmation_id: uuid.UUID) -> Confirmation | None:
        confirmation = await self.get_by_id(confirmation_id)
        if confirmation is None or confirmation.status != "pending":
            return None
        confirmation.status = "rejected"
        confirmation.resolved_at = datetime.now(UTC)
        await self._session.flush()
        return confirmation

    async def expire_stale(self) -> int:
        now = datetime.now(UTC)
        result = await self._session.execute(
            select(Confirmation).where(
                Confirmation.status == "pending",
                Confirmation.expires_at <= now,
            )
        )
        count = 0
        for confirmation in result.scalars().all():
            confirmation.status = "expired"
            count += 1
        if count:
            await self._session.flush()
        return count
