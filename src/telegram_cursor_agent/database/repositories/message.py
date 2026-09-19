"""Message repository."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.models.message import Message


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        session_id: uuid.UUID,
        role: str,
        content: str,
        message_type: str = "text",
    ) -> Message:
        message = Message(
            session_id=session_id,
            role=role,
            content=content,
            message_type=message_type,
        )
        self._session.add(message)
        await self._session.flush()
        return message

    async def list_for_session(self, session_id: uuid.UUID, limit: int = 50) -> list[Message]:
        result = await self._session.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
