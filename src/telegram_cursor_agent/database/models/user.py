"""User model."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from sqlalchemy import BigInteger, Boolean, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from telegram_cursor_agent.database.base import Base, TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from telegram_cursor_agent.database.models.project import Project
    from telegram_cursor_agent.database.models.session import AgentSession
    from telegram_cursor_agent.database.models.task import Task


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False, index=True)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    active_project_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("projects.id"), nullable=True
    )
    show_tool_calls_live: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    memory_change_notify: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )

    projects: Mapped[list[Project]] = relationship(
        back_populates="owner",
        foreign_keys="Project.owner_id",
    )
    active_project: Mapped[Project | None] = relationship(
        foreign_keys=[active_project_id],
    )
    sessions: Mapped[list[AgentSession]] = relationship(back_populates="user")
    tasks: Mapped[list[Task]] = relationship(back_populates="user")
