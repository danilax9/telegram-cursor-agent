"""Upload storage service."""

import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.models.upload import Upload
from telegram_cursor_agent.database.repositories.upload import UploadRepository


class UploadService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._repo = UploadRepository(db)
        self._storage = settings.upload_storage_path
        self._storage.mkdir(parents=True, exist_ok=True)

    async def store(
        self,
        user_id: uuid.UUID,
        filename: str,
        data: bytes,
        mime_type: str | None = None,
        project_id: uuid.UUID | None = None,
        telegram_file_id: str | None = None,
    ) -> Upload:
        if len(data) > self._settings.max_upload_bytes:
            raise ValueError(
                f"Upload exceeds max size of {self._settings.max_upload_bytes} bytes"
            )

        safe_name = Path(filename).name
        stored_name = f"{uuid.uuid4()}_{safe_name}"
        stored_path = self._storage / stored_name
        stored_path.write_bytes(data)

        return await self._repo.create(
            user_id=user_id,
            original_filename=safe_name,
            stored_path=str(stored_path),
            size_bytes=len(data),
            mime_type=mime_type,
            project_id=project_id,
            telegram_file_id=telegram_file_id,
        )

    def resolve_upload_path(self, stored_path: str) -> Path:
        path = Path(stored_path).resolve()
        storage_root = self._storage.resolve()
        if not str(path).startswith(str(storage_root)):
            raise PermissionError("Upload path outside storage root")
        return path
