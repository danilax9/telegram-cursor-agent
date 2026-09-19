"""Agent session model."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_cursor_agent.database.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from telegram_cursor_agent.database.models.message import Message
    from telegram_cursor_agent.database.models.project import Project
    from telegram_cursor_agent.database.models.task import Task
    from telegram_cursor_agent.database.models.user import User


class AgentSession(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "agent_sessions"

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id"), nullable=True
    )
    cursor_chat_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    title: Mapped[str | None] = mapped_column(String(255), nullable=True)
    workspace_path: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(50), default="active", nullable=False)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="sessions")
    project: Mapped[Project | None] = relationship(back_populates="sessions")
    messages: Mapped[list[Message]] = relationship(back_populates="session")
    tasks: Mapped[list[Task]] = relationship(back_populates="session")
