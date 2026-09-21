"""Text message handlers."""

from aiogram import Router
from aiogram.types import Message
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.agent.parser import IntentType, parse_intent
from telegram_cursor_agent.agent.prompts import CONFIRMATION_PROMPT
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import split_telegram_message
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.projects.service import ProjectService
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.actions import ActionResultType, ActionService
from telegram_cursor_agent.services.image_attachments import PendingImageStore
from telegram_cursor_agent.services.mcp_setup import McpSetupService
from telegram_cursor_agent.telegram.keyboards import confirmation_keyboard, mcp_setup_keyboard

router = Router()


@router.message()
async def handle_text_message(
    message: Message,
    db: AsyncSession,
    settings: Settings,
    telegram_user_id: int,
    task_queue: TaskQueue,
    runner: ProcessRunner,
    redis_client: Redis,  # type: ignore[type-arg]
) -> None:
    if not message.text:
        return

    users = UserRepository(db)
    user = await users.get_by_telegram_id(telegram_user_id)
    if user is None:
        await message.answer("Please send /start first.")
        return

    mcp_setup = McpSetupService(db, settings, runner, task_queue)
    mcp_reply = await mcp_setup.try_handle_pending_message(user.id, message.text)
    if mcp_reply is not None:
        await message.answer(mcp_reply)
        return

    project_service = ProjectService(db, settings)
    workspace = await project_service.resolve_workspace(user)
    agent_workspace = project_service.resolve_agent_workspace()
    intent = parse_intent(message.text)
    image_attachments = None
    if intent.intent == IntentType.AGENT_PROMPT:
        pending = PendingImageStore(redis_client)
        image_attachments = await pending.get_and_clear(user.id)
        if not image_attachments:
            image_attachments = None

    action_service = ActionService(db, settings, runner, task_queue)
    result = await action_service.handle_text(
        user.id,
        message.text,
        workspace,
        project_id=user.active_project_id,
        agent_workspace=agent_workspace,
        image_attachments=image_attachments,
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

    if result.result_type == ActionResultType.MCP_SETUP and result.confirmation_id:
        await message.answer(
            result.message,
            reply_markup=mcp_setup_keyboard(result.confirmation_id),
        )
        return

    for chunk in split_telegram_message(result.message):
        await message.answer(chunk)
