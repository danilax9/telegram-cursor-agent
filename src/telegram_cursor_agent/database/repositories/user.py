"""User repository."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.models.user import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self._session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def get_by_id(self, user_id: uuid.UUID) -> User | None:
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def upsert(
        self,
        telegram_id: int,
        username: str | None,
        is_admin: bool,
    ) -> User:
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            user = User(telegram_id=telegram_id, username=username, is_admin=is_admin)
            self._session.add(user)
        else:
            user.username = username
            user.is_admin = is_admin
        await self._session.flush()
        return user

    async def set_active_project(
        self, user_id: uuid.UUID, project_id: uuid.UUID | None
    ) -> User | None:
        user = await self.get_by_id(user_id)
        if user is None:
            return None
        user.active_project_id = project_id
        await self._session.flush()
        return user
