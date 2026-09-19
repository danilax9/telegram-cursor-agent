"""Document upload handlers."""

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.services.uploads import UploadService

router = Router()


@router.message(lambda m: m.document is not None)
async def handle_document_upload(
    message: Message,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
) -> None:
    if not message.document:
        return

    users = UserRepository(db)
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        await message.answer("Please send /start first.")
        return

    if message.document.file_size and message.document.file_size > settings.max_upload_bytes:
        await message.answer(f"File too large. Max: {settings.max_upload_bytes} bytes.")
        return

    bot = message.bot
    if bot is None:
        return

    file = await bot.get_file(message.document.file_id)
    if file.file_path is None:
        await message.answer("Could not download file.")
        return

    downloaded = await bot.download_file(file.file_path)
    if downloaded is None:
        await message.answer("Download failed.")
        return

    data = downloaded.read()
    service = UploadService(db, settings)
    try:
        upload = await service.store(
            user_id=user.id,
            filename=message.document.file_name or "upload.bin",
            data=data,
            mime_type=message.document.mime_type,
            telegram_file_id=message.document.file_id,
        )
    except ValueError as exc:
        await message.answer(str(exc))
        return

    await message.answer(
        f"Uploaded: {upload.original_filename}\n"
        f"Size: {upload.size_bytes} bytes\n"
        f"ID: {upload.id}"
    )
