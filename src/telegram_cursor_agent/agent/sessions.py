"""Agent session management."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.session_titles import (
    title_from_cursor_transcript,
    title_from_prompt,
)
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.models.message import Message
from telegram_cursor_agent.database.models.session import AgentSession
from telegram_cursor_agent.database.repositories.message import MessageRepository
from telegram_cursor_agent.database.repositories.session import SessionRepository
from telegram_cursor_agent.execution.sandbox import assert_path_allowed
from telegram_cursor_agent.services.cursor_models import load_selected_model_id
from telegram_cursor_agent.services.opencode_models import is_opencode_model


class SessionError(Exception):
    pass


def engine_for_model(model_id: str | None) -> str:
    return "opencode" if is_opencode_model(model_id) else "cursor"


def engine_of(session: AgentSession) -> str:
    stored = (session.engine or "").strip()
    if stored in {"cursor", "opencode"}:
        return stored
    if session.opencode_session_id and not session.cursor_chat_id:
        return "opencode"
    return "cursor"


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
        engine = engine_for_model(load_selected_model_id(self._settings))
        active = await self._sessions.get_active_for_user(user_id)
        if active is None:
            safe_path = assert_path_allowed(workspace_path, self._settings)
            return await self._sessions.create(
                user_id=user_id,
                workspace_path=str(safe_path),
                project_id=project_id,
                engine=engine,
            )
        if engine_of(active) == engine:
            return active
        session, _action = await self.align_to_engine(
            user_id,
            engine,
            workspace_path,
            project_id,
        )
        return session

    async def get_active(self, user_id: uuid.UUID) -> AgentSession | None:
        active = await self._sessions.get_active_for_user(user_id)
        if active is not None:
            await self.fill_missing_titles([active])
        return active

    async def list_resumable(
        self, user_id: uuid.UUID, *, limit: int = 20
    ) -> list[AgentSession]:
        sessions = await self._sessions.list_for_user(
            user_id,
            limit=limit,
            exclude_deleted=True,
        )
        await self.fill_missing_titles(sessions)
        return sessions

    async def create_new(
        self,
        user_id: uuid.UUID,
        workspace_path: str,
        project_id: uuid.UUID | None = None,
    ) -> AgentSession:
        await self._sessions.archive_all_for_user(user_id)
        safe_path = assert_path_allowed(workspace_path, self._settings)
        engine = engine_for_model(load_selected_model_id(self._settings))
        return await self._sessions.create(
            user_id=user_id,
            workspace_path=str(safe_path),
            project_id=project_id,
            engine=engine,
        )

    async def align_to_engine(
        self,
        user_id: uuid.UUID,
        engine: str,
        workspace_path: str,
        project_id: uuid.UUID | None = None,
    ) -> tuple[AgentSession, str]:
        """Activate the latest session of this engine, or start a new one."""
        target = engine if engine in {"cursor", "opencode"} else "cursor"
        active = await self._sessions.get_active_for_user(user_id)
        if active is not None and engine_of(active) == target:
            if active.engine != target:
                active.engine = target
                await self._db.flush()
            return active, "same"

        carried_opencode: str | None = None
        if (
            target == "opencode"
            and active is not None
            and active.opencode_session_id
            and engine_of(active) != "opencode"
        ):
            carried_opencode = active.opencode_session_id
            active.opencode_session_id = None

        history = await self._sessions.list_for_user(
            user_id, limit=50, exclude_deleted=True
        )
        match = next(
            (
                session
                for session in history
                if engine_of(session) == target
                and (active is None or session.id != active.id)
            ),
            None,
        )
        await self._sessions.archive_all_for_user(user_id)
        if match is not None:
            if target == "opencode" and carried_opencode and not match.opencode_session_id:
                match.opencode_session_id = carried_opencode
            match.engine = target
            activated = await self._sessions.set_status(match.id, "active")
            if activated is None:
                raise SessionError("Не удалось открыть сессию.")
            await self.fill_missing_titles([activated])
            return activated, "resumed"

        safe_path = assert_path_allowed(workspace_path, self._settings)
        created = await self._sessions.create(
            user_id=user_id,
            workspace_path=str(safe_path),
            project_id=project_id,
            engine=target,
            opencode_session_id=carried_opencode if target == "opencode" else None,
        )
        return created, "created"

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

    async def rename(
        self, user_id: uuid.UUID, session_id: uuid.UUID, title: str
    ) -> AgentSession:
        agent_session = await self._sessions.get_for_user(session_id, user_id)
        if agent_session is None or agent_session.status == "deleted":
            raise SessionError("Сессия не найдена.")
        clean = " ".join(title.split())
        if not clean:
            raise SessionError("Имя не может быть пустым.")
        updated = await self._sessions.set_title(session_id, clean[:80])
        if updated is None:
            raise SessionError("Не удалось переименовать сессию.")
        return updated

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

    async def ensure_title_from_prompt(
        self, agent_session: AgentSession, prompt: str
    ) -> None:
        if (agent_session.title or "").strip():
            return
        title = title_from_prompt(prompt)
        if not title:
            return
        await self._sessions.set_title(agent_session.id, title)
        agent_session.title = title

    async def fill_missing_titles(self, sessions: list[AgentSession]) -> None:
        changed = False
        for agent_session in sessions:
            if (agent_session.title or "").strip():
                continue
            chat_id = agent_session.cursor_chat_id or ""
            title = title_from_cursor_transcript(self._settings, chat_id)
            if not title:
                continue
            agent_session.title = title
            changed = True
        if changed:
            await self._db.flush()

    async def resolve_selector(
        self, user_id: uuid.UUID, selector: str, *, limit: int = 20
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
