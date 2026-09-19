"""Project model."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_cursor_agent.database.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from telegram_cursor_agent.database.models.session import AgentSession
    from telegram_cursor_agent.database.models.user import User


class Project(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "projects"

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    root_path: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("users.id"), nullable=False)

    owner: Mapped[User] = relationship(
        back_populates="projects",
        foreign_keys=[owner_id],
    )
    sessions: Mapped[list[AgentSession]] = relationship(back_populates="project")
