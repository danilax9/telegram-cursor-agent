"""Cursor account login worker delegation tests."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram_cursor_agent.services.cursor_account_login import (
    CURSOR_LOGIN_TASK_NOTIFIED,
    CursorAccountLoginService,
)


@pytest.mark.asyncio
async def test_queue_login_start_creates_worker_task(test_settings) -> None:
    service = CursorAccountLoginService(test_settings, MagicMock(), MagicMock())
    service._has_pending = AsyncMock(return_value=False)  # type: ignore[method-assign]

    db = MagicMock()
    db.commit = AsyncMock()
    task_queue = MagicMock()
    task_queue.enqueue = AsyncMock()

    created_task = MagicMock()
    created_task.id = "task-1"
    tasks_repo = MagicMock()
    tasks_repo.create = AsyncMock(return_value=created_task)
    service._tasks_repo = tasks_repo

    from telegram_cursor_agent.services import cursor_account_login as module

    original_repo = module.TaskRepository
    module.TaskRepository = MagicMock(return_value=tasks_repo)
    try:
        text = await service.queue_login_start(
            db,
            task_queue,
            user_id="user-id",
            telegram_id=42,
            account_id="backup",
        )
    finally:
        module.TaskRepository = original_repo

    assert "backup" in text
    tasks_repo.create.assert_awaited_once()
    assert tasks_repo.create.await_args.kwargs["task_type"] == "cursor_account_login"
    db.commit.assert_awaited()
    task_queue.enqueue.assert_awaited_once_with("task-1")


def test_is_cli_available_false_when_missing(test_settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "telegram_cursor_agent.services.cursor_account_login.shutil.which",
        lambda _name: None,
    )
    test_settings.cursor_cli_path = "missing-cursor-agent"
    service = CursorAccountLoginService(test_settings, MagicMock(), MagicMock())
    assert service.is_cli_available() is False


def test_cursor_login_task_notified_constant() -> None:
    assert CURSOR_LOGIN_TASK_NOTIFIED


@pytest.mark.asyncio
async def test_queue_account_switch_creates_worker_task(test_settings) -> None:
    service = CursorAccountLoginService(test_settings, MagicMock(), MagicMock())

    db = MagicMock()
    db.commit = AsyncMock()
    task_queue = MagicMock()
    task_queue.enqueue = AsyncMock()

    created_task = MagicMock()
    created_task.id = "task-2"
    tasks_repo = MagicMock()
    tasks_repo.create = AsyncMock(return_value=created_task)

    from telegram_cursor_agent.services import cursor_account_login as module

    original_repo = module.TaskRepository
    module.TaskRepository = MagicMock(return_value=tasks_repo)
    try:
        text = await service.queue_account_switch(
            db,
            task_queue,
            user_id="user-id",
            telegram_id=42,
            account_id="backup",
        )
    finally:
        module.TaskRepository = original_repo

    assert "backup" in text
    assert tasks_repo.create.await_args.kwargs["task_type"] == "cursor_account_switch"
    db.commit.assert_awaited()
    task_queue.enqueue.assert_awaited_once_with("task-2")
