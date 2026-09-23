"""Document and photo upload handlers."""

from aiogram import F, Router
from aiogram.types import Message
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import split_telegram_message
from telegram_cursor_agent.database.models.upload import Upload
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.projects.service import ProjectService
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.agent.sessions import SessionService
from telegram_cursor_agent.services.actions import ActionResultType, ActionService
from telegram_cursor_agent.telegram.session_live import (
    REDIRECT_STATUS_TEXT,
    edit_session_live_via_message,
)
from telegram_cursor_agent.telegram.typing_indicator import send_typing
from telegram_cursor_agent.services.image_attachments import PendingImageStore, StoredImage
from telegram_cursor_agent.services.uploads import UploadService
from telegram_cursor_agent.telegram.media_groups import schedule_media_group

router = Router()

IMAGE_MIME_PREFIX = "image/"


async def _download_file_data(message: Message, file_id: str) -> bytes | None:
    bot = message.bot
    if bot is None:
        return None
    file = await bot.get_file(file_id)
    if file.file_path is None:
        return None
    downloaded = await bot.download_file(file.file_path)
    if downloaded is None:
        return None
    return downloaded.read()


async def _store_upload(
    db: AsyncSession,
    settings: Settings,
    user_id,
    filename: str,
    data: bytes,
    mime_type: str | None,
    telegram_file_id: str,
) -> Upload:
    service = UploadService(db, settings)
    return await service.store(
        user_id=user_id,
        filename=filename,
        data=data,
        mime_type=mime_type,
        telegram_file_id=telegram_file_id,
    )


def _stored_image(upload: Upload) -> StoredImage:
    return StoredImage(
        path=upload.stored_path,
        filename=upload.original_filename,
        mime_type=upload.mime_type,
    )


async def _reply_action_result(
    message: Message,
    result,
    db: AsyncSession,
    settings: Settings,
    redis_client: Redis,  # type: ignore[type-arg]
    user_id,
) -> None:
    if result.result_type == ActionResultType.REDIRECT_REQUESTED:
        await send_typing(message)
        agent_session = await SessionService(db, settings).get_active(user_id)
        if agent_session is not None:
            await edit_session_live_via_message(
                redis_client,
                message,
                agent_session.id,
                REDIRECT_STATUS_TEXT,
                settings,
            )
        return
    if result.result_type == ActionResultType.TASK_QUEUED:
        if result.typing_indicator:
            await send_typing(message)
        return
    for chunk in split_telegram_message(result.message):
        await message.answer(chunk)


async def _queue_images_with_caption(
    message: Message,
    db: AsyncSession,
    settings: Settings,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    redis_client: Redis,  # type: ignore[type-arg]
    user,
    uploads: list[Upload],
    caption: str | None,
) -> None:
    project_service = ProjectService(db, settings)
    workspace = await project_service.resolve_workspace(user)
    agent_workspace = project_service.resolve_agent_workspace()
    action_service = ActionService(db, settings, runner, task_queue, redis_client)
    images = [_stored_image(upload) for upload in uploads]
    text = caption or ""
    if text.strip():
        await send_typing(message)
    result = await action_service.handle_text(
        user.id,
        text,
        workspace,
        project_id=user.active_project_id,
        agent_workspace=agent_workspace,
        image_attachments=images,
    )
    await _reply_action_result(
        message, result, db, settings, redis_client, user.id
    )


async def _save_pending_images(
    message: Message,
    redis_client: Redis,  # type: ignore[type-arg]
    user_id,
    uploads: list[Upload],
) -> None:
    pending = PendingImageStore(redis_client)
    for upload in uploads:
        await pending.add(user_id, _stored_image(upload))
    count = len(uploads)
    noun = "Фото сохранено" if count == 1 else f"Сохранено фото: {count}"
    await message.answer(
        f"{noun}. Напиши, что с ним сделать — я передам Cursor вместе с изображением."
    )


async def _process_image_messages(
    messages: list[Message],
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    redis_client: Redis,  # type: ignore[type-arg]
) -> None:
    users = UserRepository(db)
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        await messages[0].answer("Please send /start first.")
        return

    uploads: list[Upload] = []
    for item in messages:
        if item.photo:
            photo = item.photo[-1]
            if photo.file_size and photo.file_size > settings.max_upload_bytes:
                await item.answer(
                    f"File too large. Max: {settings.max_upload_bytes} bytes."
                )
                return
            data = await _download_file_data(item, photo.file_id)
            if data is None:
                await item.answer("Could not download photo.")
                return
            upload = await _store_upload(
                db,
                settings,
                user.id,
                filename=f"photo_{photo.file_unique_id}.jpg",
                data=data,
                mime_type="image/jpeg",
                telegram_file_id=photo.file_id,
            )
            uploads.append(upload)
            continue

        if item.document and _is_image_document(item.document):
            document = item.document
            if document.file_size and document.file_size > settings.max_upload_bytes:
                await item.answer(
                    f"File too large. Max: {settings.max_upload_bytes} bytes."
                )
                return
            data = await _download_file_data(item, document.file_id)
            if data is None:
                await item.answer("Could not download file.")
                return
            upload = await _store_upload(
                db,
                settings,
                user.id,
                filename=document.file_name or "image.bin",
                data=data,
                mime_type=document.mime_type,
                telegram_file_id=document.file_id,
            )
            uploads.append(upload)

    if not uploads:
        return

    caption = next((item.caption for item in messages if item.caption), None)
    first = messages[0]
    if caption:
        await _queue_images_with_caption(
            first,
            db,
            settings,
            task_queue,
            runner,
            redis_client,
            user,
            uploads,
            caption,
        )
    else:
        await _save_pending_images(first, redis_client, user.id, uploads)


def _is_image_document(document) -> bool:
    mime = document.mime_type or ""
    return mime.startswith(IMAGE_MIME_PREFIX)


@router.message(F.photo)
async def handle_photo_upload(
    message: Message,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    redis_client: Redis,  # type: ignore[type-arg]
) -> None:
    await schedule_media_group(
        message,
        lambda messages: _process_image_messages(
            messages,
            db,
            settings,
            telegram_user_id,
            task_queue,
            runner,
            redis_client,
        ),
    )


@router.message(lambda m: m.document is not None and not _is_image_document(m.document))
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

    data = await _download_file_data(message, message.document.file_id)
    if data is None:
        await message.answer("Could not download file.")
        return

    try:
        upload = await _store_upload(
            db,
            settings,
            user.id,
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


@router.message(
    lambda m: m.document is not None and _is_image_document(m.document) and m.photo is None
)
async def handle_image_document_upload(
    message: Message,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    redis_client: Redis,  # type: ignore[type-arg]
) -> None:
    await schedule_media_group(
        message,
        lambda messages: _process_image_messages(
            messages,
            db,
            settings,
            telegram_user_id,
            task_queue,
            runner,
            redis_client,
        ),
    )
