"""Upload repository."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.models.upload import Upload


class UploadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        user_id: uuid.UUID,
        original_filename: str,
        stored_path: str,
        size_bytes: int,
        mime_type: str | None = None,
        project_id: uuid.UUID | None = None,
        telegram_file_id: str | None = None,
    ) -> Upload:
        upload = Upload(
            user_id=user_id,
            original_filename=original_filename,
            stored_path=stored_path,
            size_bytes=size_bytes,
            mime_type=mime_type,
            project_id=project_id,
            telegram_file_id=telegram_file_id,
        )
        self._session.add(upload)
        await self._session.flush()
        return upload

    async def get_by_id(self, upload_id: uuid.UUID) -> Upload | None:
        result = await self._session.execute(select(Upload).where(Upload.id == upload_id))
        return result.scalar_one_or_none()

    async def list_by_user(self, user_id: uuid.UUID, limit: int = 20) -> list[Upload]:
        result = await self._session.execute(
            select(Upload)
            .where(Upload.user_id == user_id)
            .order_by(Upload.created_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())
