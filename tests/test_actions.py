"""Action service integration tests."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.services.actions import ActionResultType, ActionService
from telegram_cursor_agent.services.image_attachments import StoredImage


@pytest.fixture
def mock_queue() -> MagicMock:
    queue = MagicMock()
    queue.enqueue = AsyncMock()
    return queue


async def _create_user(db: AsyncSession) -> uuid.UUID:
    users = UserRepository(db)
    user = await users.upsert(telegram_id=12345, username="admin", is_admin=True)
    return user.id


async def test_help_action(
    db_session: AsyncSession, test_settings, runner: ProcessRunner, mock_queue
) -> None:
    user_id = await _create_user(db_session)
    service = ActionService(db_session, test_settings, runner, mock_queue)
    result = await service.handle_text(user_id, "help", str(test_settings.workspace_base))
    assert result.result_type == ActionResultType.TEXT
    assert "Available commands" in result.message


async def test_agent_prompt_queues_task(
    db_session: AsyncSession, test_settings, runner: ProcessRunner, mock_queue
) -> None:
    user_id = await _create_user(db_session)
    service = ActionService(db_session, test_settings, runner, mock_queue)
    result = await service.handle_text(
        user_id, "implement feature X", str(test_settings.workspace_base)
    )
    assert result.result_type == ActionResultType.TASK_QUEUED
    mock_queue.enqueue.assert_called_once()


async def test_agent_prompt_with_image_includes_path(
    db_session: AsyncSession, test_settings, runner: ProcessRunner, mock_queue
) -> None:
    user_id = await _create_user(db_session)
    service = ActionService(db_session, test_settings, runner, mock_queue)
    stored_path = "/data/uploads/test.jpg"
    result = await service.handle_text(
        user_id,
        "опиши картинку",
        str(test_settings.workspace_base),
        image_attachments=[
            StoredImage(
                path=stored_path,
                filename="test.jpg",
                mime_type="image/jpeg",
            )
        ],
    )
    assert result.result_type == ActionResultType.TASK_QUEUED
    tasks = TaskRepository(db_session)
    task = await tasks.get_by_id(result.task_id)
    assert task is not None
    agent_path = str(test_settings.agent_upload_storage_path / "test.jpg")
    assert agent_path in (task.payload or "")


async def test_sensitive_command_requires_confirmation(
    db_session: AsyncSession, test_settings, runner: ProcessRunner, mock_queue
) -> None:
    user_id = await _create_user(db_session)
    service = ActionService(db_session, test_settings, runner, mock_queue)
    result = await service.handle_text(
        user_id, "run git push origin main", str(test_settings.workspace_base)
    )
    assert result.result_type == ActionResultType.CONFIRMATION_REQUIRED
    assert result.confirmation_id is not None


async def test_summarize_requires_existing_chat(
    db_session: AsyncSession, test_settings, runner: ProcessRunner, mock_queue
) -> None:
    user_id = await _create_user(db_session)
    service = ActionService(db_session, test_settings, runner, mock_queue)
    result = await service.queue_session_slash_command(
        user_id,
        "/summarize",
        str(test_settings.projects_root),
        project_id=None,
    )
    assert result.result_type == ActionResultType.ERROR


async def test_summarize_queues_task(
    db_session: AsyncSession, test_settings, runner: ProcessRunner, mock_queue
) -> None:
    user_id = await _create_user(db_session)
    service = ActionService(db_session, test_settings, runner, mock_queue)
    session = await service._sessions.get_or_create_active(
        user_id, str(test_settings.projects_root)
    )
    await service._sessions.set_cursor_chat_id(session.id, "chat-abc")

    result = await service.queue_session_slash_command(
        user_id,
        "/summarize",
        str(test_settings.projects_root),
        project_id=None,
    )
    assert result.result_type == ActionResultType.TASK_QUEUED
    mock_queue.enqueue.assert_called_once()


async def test_forbidden_command(
    db_session: AsyncSession, test_settings, runner: ProcessRunner, mock_queue
) -> None:
    user_id = await _create_user(db_session)
    service = ActionService(db_session, test_settings, runner, mock_queue)
    result = await service.handle_text(
        user_id, "run rm -rf /", str(test_settings.workspace_base)
    )
    assert result.result_type == ActionResultType.ERROR
