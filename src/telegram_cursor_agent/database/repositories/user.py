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
        *,
        is_admin: bool | None = None,
    ) -> User:
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            user = User(
                telegram_id=telegram_id,
                username=username,
                is_admin=is_admin or False,
            )
            self._session.add(user)
        else:
            if username is not None:
                user.username = username
            if is_admin is not None:
                user.is_admin = is_admin
        await self._session.flush()
        return user

    async def list_authorized(self) -> list[User]:
        result = await self._session.execute(
            select(User).where(User.is_admin.is_(True)).order_by(User.created_at)
        )
        return list(result.scalars().all())

    async def get_by_username(self, username: str) -> User | None:
        normalized = username.lstrip("@").lower()
        result = await self._session.execute(
            select(User).where(User.username.is_not(None))
        )
        for user in result.scalars().all():
            if user.username and user.username.lower() == normalized:
                return user
        return None

    async def grant_access(
        self,
        telegram_id: int,
        username: str | None = None,
    ) -> User:
        return await self.upsert(telegram_id, username, is_admin=True)

    async def revoke_access(self, telegram_id: int) -> User | None:
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            return None
        user.is_admin = False
        await self._session.flush()
        return user

    async def toggle_show_tool_calls_live(self, user_id: uuid.UUID) -> User | None:
        user = await self.get_by_id(user_id)
        if user is None:
            return None
        user.show_tool_calls_live = not user.show_tool_calls_live
        await self._session.flush()
        return user

    async def toggle_memory_change_notify(self, user_id: uuid.UUID) -> User | None:
        user = await self.get_by_id(user_id)
        if user is None:
            return None
        user.memory_change_notify = not user.memory_change_notify
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
