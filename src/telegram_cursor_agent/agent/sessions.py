"""Agent session management."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.models.message import Message
from telegram_cursor_agent.database.models.session import AgentSession
from telegram_cursor_agent.database.repositories.message import MessageRepository
from telegram_cursor_agent.database.repositories.session import SessionRepository
from telegram_cursor_agent.execution.sandbox import assert_path_allowed


class SessionError(Exception):
    pass


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

    async def get_active(self, user_id: uuid.UUID) -> AgentSession | None:
        return await self._sessions.get_active_for_user(user_id)

    async def list_resumable(
        self, user_id: uuid.UUID, *, limit: int = 10
    ) -> list[AgentSession]:
        return await self._sessions.list_for_user(
            user_id,
            limit=limit,
            exclude_deleted=True,
        )

    async def create_new(
        self,
        user_id: uuid.UUID,
        workspace_path: str,
        project_id: uuid.UUID | None = None,
    ) -> AgentSession:
        await self._sessions.archive_all_for_user(user_id)
        safe_path = assert_path_allowed(workspace_path, self._settings)
        return await self._sessions.create(
            user_id=user_id,
            workspace_path=str(safe_path),
            project_id=project_id,
        )

    async def activate(
        self, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> AgentSession:
        agent_session = await self._sessions.get_for_user(session_id, user_id)
        if agent_session is None:
            raise SessionError("Сессия не найдена.")
        if agent_session.status == "deleted":
            raise SessionError("Сессия уже удалена.")
        await self._sessions.archive_all_for_user(user_id)
        activated = await self._sessions.set_status(session_id, "active")
        if activated is None:
            raise SessionError("Не удалось активировать сессию.")
        return activated

    async def delete(
        self, user_id: uuid.UUID, session_id: uuid.UUID
    ) -> AgentSession:
        agent_session = await self._sessions.get_for_user(session_id, user_id)
        if agent_session is None:
            raise SessionError("Сессия не найдена.")
        if agent_session.status == "deleted":
            raise SessionError("Сессия уже удалена.")
        deleted = await self._sessions.set_status(session_id, "deleted")
        if deleted is None:
            raise SessionError("Не удалось удалить сессию.")
        return deleted

    async def resolve_selector(
        self, user_id: uuid.UUID, selector: str, *, limit: int = 10
    ) -> AgentSession:
        sessions = await self.list_resumable(user_id, limit=limit)
        if not sessions:
            raise SessionError("Нет сохранённых сессий.")

        normalized = selector.strip().lower()
        if normalized.isdigit():
            index = int(normalized) - 1
            if index < 0 or index >= len(sessions):
                raise SessionError(f"Нет сессии с номером {normalized}.")
            return sessions[index]

        matches = [
            session
            for session in sessions
            if str(session.id).lower().startswith(normalized)
            or (session.cursor_chat_id or "").lower().startswith(normalized)
        ]
        if not matches:
            raise SessionError(f"Сессия `{selector}` не найдена.")
        if len(matches) > 1:
            raise SessionError(
                f"Неоднозначный идентификатор `{selector}`. Уточни номер или UUID."
            )
        return matches[0]

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

    async def set_title_if_empty(
        self,
        session_id: uuid.UUID,
        user_message: str,
        assistant_message: str,
    ) -> AgentSession | None:
        from telegram_cursor_agent.agent.session_title import generate_session_title

        agent_session = await self._sessions.get_by_id(session_id)
        if agent_session is None or agent_session.title:
            return agent_session
        title = generate_session_title(user_message, assistant_message)
        return await self._sessions.update_title(session_id, title)
