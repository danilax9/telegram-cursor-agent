"""Text message handlers."""

from aiogram import Router
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.prompts import CONFIRMATION_PROMPT
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import split_telegram_message
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.projects.service import ProjectService
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.actions import ActionResultType, ActionService
from telegram_cursor_agent.telegram.keyboards import confirmation_keyboard

router = Router()


@router.message()
async def handle_text_message(
    message: Message,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
) -> None:
    if not message.text:
        return

    users = UserRepository(db)
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        await message.answer("Please send /start first.")
        return

    project_service = ProjectService(db, settings)
    workspace = await project_service.resolve_workspace(user)
    action_service = ActionService(db, settings, runner, task_queue)
    result = await action_service.handle_text(
        user.id, message.text, workspace, project_id=user.active_project_id
    )

    if result.result_type == ActionResultType.CONFIRMATION_REQUIRED:
        text = CONFIRMATION_PROMPT.format(
            action=result.message,
            ttl=settings.confirmation_ttl_seconds,
        )
        if result.confirmation_id:
            await message.answer(
                text,
                reply_markup=confirmation_keyboard(result.confirmation_id),
            )
        else:
            await message.answer(text)
        return

    if result.result_type == ActionResultType.TASK_QUEUED:
        # Cursor's typing indicator and final response are the only UX for
        # ordinary prompts; an enqueue acknowledgement is just noise.
        return

    for chunk in split_telegram_message(result.message):
        await message.answer(chunk)
