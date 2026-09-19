"""Confirmation workflow service."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.models.confirmation import Confirmation
from telegram_cursor_agent.database.repositories.confirmation import ConfirmationRepository


class ConfirmationService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._repo = ConfirmationRepository(db)

    async def approve(
        self, confirmation_id: uuid.UUID, user_id: uuid.UUID
    ) -> Confirmation | None:
        confirmation = await self._repo.get_by_id(confirmation_id)
        if confirmation is None:
            return None
        if confirmation.user_id != user_id:
            raise PermissionError("Not your confirmation")
        return await self._repo.approve(confirmation_id)

    async def reject(
        self, confirmation_id: uuid.UUID, user_id: uuid.UUID
    ) -> Confirmation | None:
        confirmation = await self._repo.get_by_id(confirmation_id)
        if confirmation is None:
            return None
        if confirmation.user_id != user_id:
            raise PermissionError("Not your confirmation")
        return await self._repo.reject(confirmation_id)

    async def expire_stale(self) -> int:
        return await self._repo.expire_stale()
