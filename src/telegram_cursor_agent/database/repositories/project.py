"""Project repository."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.models.project import Project


class ProjectRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_id(self, project_id: uuid.UUID) -> Project | None:
        result = await self._session.execute(select(Project).where(Project.id == project_id))
        return result.scalar_one_or_none()

    async def get_by_root_path(self, root_path: str) -> Project | None:
        result = await self._session.execute(
            select(Project).where(Project.root_path == root_path)
        )
        return result.scalar_one_or_none()

    async def list_by_owner(self, owner_id: uuid.UUID) -> list[Project]:
        result = await self._session.execute(
            select(Project).where(Project.owner_id == owner_id).order_by(Project.name)
        )
        return list(result.scalars().all())

    async def create(self, name: str, root_path: str, owner_id: uuid.UUID) -> Project:
        project = Project(name=name, root_path=root_path, owner_id=owner_id)
        self._session.add(project)
        await self._session.flush()
        return project
