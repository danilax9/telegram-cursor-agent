"""Agent session management."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.models.message import Message
from telegram_cursor_agent.database.models.session import AgentSession
from telegram_cursor_agent.database.repositories.message import MessageRepository
from telegram_cursor_agent.database.repositories.session import SessionRepository
from telegram_cursor_agent.execution.sandbox import assert_path_allowed


class SessionService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._sessions = SessionRepository(db)
        self._messages = MessageRepository(db)

    async def get_or_create_active(
        self,
        user_id: uuid.UUID,
        workspace_path: str,
        project_id: uuid.UUID | None = None,
    ) -> AgentSession:
        active = await self._sessions.get_active_for_user(user_id)
        if active is not None:
            return active

        safe_path = assert_path_allowed(workspace_path, self._settings)
        return await self._sessions.create(
            user_id=user_id,
            workspace_path=str(safe_path),
            project_id=project_id,
        )

    async def record_message(
        self,
        session_id: uuid.UUID,
        role: str,
        content: str,
        message_type: str = "text",
    ) -> Message:
        return await self._messages.create(session_id, role, content, message_type)

    async def set_cursor_chat_id(
        self, session_id: uuid.UUID, cursor_chat_id: str
    ) -> AgentSession | None:
        return await self._sessions.update_cursor_chat_id(session_id, cursor_chat_id)
