"""Agent session repository."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.models.session import AgentSession


class SessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, session_id: uuid.UUID) -> AgentSession | None:
        result = await self._session.execute(
            select(AgentSession).where(AgentSession.id == session_id)
        )
        return result.scalar_one_or_none()

    async def get_active_for_user(self, user_id: uuid.UUID) -> AgentSession | None:
        result = await self._session.execute(
            select(AgentSession)
            .where(AgentSession.user_id == user_id, AgentSession.status == "active")
            .order_by(AgentSession.last_active_at.desc().nullslast())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        user_id: uuid.UUID,
        workspace_path: str,
        project_id: uuid.UUID | None = None,
    ) -> AgentSession:
        agent_session = AgentSession(
            user_id=user_id,
            project_id=project_id,
            workspace_path=workspace_path,
            status="active",
            last_active_at=datetime.now(UTC),
        )
        self._session.add(agent_session)
        await self._session.flush()
        return agent_session

    async def update_cursor_chat_id(
        self, session_id: uuid.UUID, cursor_chat_id: str
    ) -> AgentSession | None:
        agent_session = await self.get_by_id(session_id)
        if agent_session is None:
            return None
        agent_session.cursor_chat_id = cursor_chat_id
        agent_session.last_active_at = datetime.now(UTC)
        await self._session.flush()
        return agent_session

    async def touch(self, session_id: uuid.UUID) -> None:
        agent_session = await self.get_by_id(session_id)
        if agent_session:
            agent_session.last_active_at = datetime.now(UTC)
            await self._session.flush()
