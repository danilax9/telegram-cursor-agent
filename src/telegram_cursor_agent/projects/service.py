"""Project service orchestrating discovery and persistence."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.models.project import Project
from telegram_cursor_agent.database.models.user import User
from telegram_cursor_agent.database.repositories.project import ProjectRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.projects.discovery import (
    DiscoveredProject,
    discover_projects,
    find_project_by_name,
    validate_project_path,
)


class ProjectService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._repo = ProjectRepository(db)

    def discover(self) -> list[DiscoveredProject]:
        return discover_projects(self._settings)

    async def sync_discovered(self, owner_id: uuid.UUID) -> list[Project]:
        discovered = self.discover()
        synced = []
        for item in discovered:
            existing = await self._repo.get_by_root_path(str(item.root_path))
            if existing is None:
                existing = await self._repo.create(
                    name=item.name,
                    root_path=str(item.root_path),
                    owner_id=owner_id,
                )
            synced.append(existing)
        return synced

    async def select_by_name(self, owner_id: uuid.UUID, name: str) -> Project | None:
        discovered = find_project_by_name(name, self._settings)
        if discovered is None:
            return None
        existing = await self._repo.get_by_root_path(str(discovered.root_path))
        if existing is None:
            existing = await self._repo.create(
                name=discovered.name,
                root_path=str(discovered.root_path),
                owner_id=owner_id,
            )
        await UserRepository(self._db).set_active_project(owner_id, existing.id)
        return existing

    async def resolve_workspace(self, user: User) -> str:
        if user.active_project_id is not None:
            project = await self._repo.get_by_id(user.active_project_id)
            if project is not None:
                return project.root_path
        return str(self._settings.projects_root)

    async def list_for_user(self, owner_id: uuid.UUID) -> list[Project]:
        return await self._repo.list_by_owner(owner_id)

    def validate_path(self, path: str) -> str:
        return str(validate_project_path(path, self._settings))
