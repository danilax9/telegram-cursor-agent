"""Upload service tests."""


import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.services.uploads import UploadService, agent_visible_upload_path


async def test_store_upload(db_session: AsyncSession, test_settings) -> None:
    users = UserRepository(db_session)
    user = await users.upsert(telegram_id=12345, username="admin", is_admin=True)

    service = UploadService(db_session, test_settings)
    upload = await service.store(
        user_id=user.id,
        filename="test.py",
        data=b"print('hello')",
        mime_type="text/plain",
    )
    assert upload.original_filename == "test.py"
    assert upload.size_bytes == 14


async def test_reject_oversized(db_session: AsyncSession, test_settings) -> None:
    users = UserRepository(db_session)
    user = await users.upsert(telegram_id=12345, username="admin", is_admin=True)

    limited_settings = test_settings.model_copy(update={"max_upload_bytes": 10})
    service = UploadService(db_session, limited_settings)
    with pytest.raises(ValueError, match="exceeds max size"):
        await service.store(user_id=user.id, filename="big.bin", data=b"x" * 100)


def test_agent_visible_upload_path_maps_container_to_host(test_settings) -> None:
    settings = test_settings.model_copy(
        update={
            "upload_storage_path": "/data/uploads",
            "upload_host_path": "/root/telegram-cursor-agent/data/uploads",
        }
    )
    mapped = agent_visible_upload_path(
        "/data/uploads/abc_photo.jpg",
        settings,
    )
    assert mapped == "/root/telegram-cursor-agent/data/uploads/abc_photo.jpg"


async def test_resolve_upload_path_traversal(
    db_session: AsyncSession, test_settings
) -> None:
    service = UploadService(db_session, test_settings)
    with pytest.raises(PermissionError):
        service.resolve_upload_path("/etc/passwd")
