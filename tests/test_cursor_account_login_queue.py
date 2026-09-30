"""Cursor account login worker delegation tests."""

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from telegram_cursor_agent.services.cursor_account_login import (
    CURSOR_LOGIN_TASK_NOTIFIED,
    CursorAccountLoginService,
    LoginStartResult,
)


def _redis_stub() -> MagicMock:
    """Redis double good enough for pending-session bookkeeping."""
    redis = MagicMock()
    redis.get = AsyncMock(return_value=None)
    redis.set = AsyncMock()
    redis.delete = AsyncMock()
    return redis


@contextmanager
def _fake_auth_file():
    """Pretend the Cursor auth file holds a real session after a login."""
    real_is_file = Path.is_file
    real_read_text = Path.read_text

    def fake_is_file(self: Path) -> bool:
        return "nonexistent-auth" in str(self) or real_is_file(self)

    def fake_read_text(self: Path, *args, **kwargs) -> str:
        if "nonexistent-auth" in str(self):
            return json.dumps({"accessToken": "token-a"})
        return real_read_text(self, *args, **kwargs)

    Path.is_file = fake_is_file  # type: ignore[method-assign]
    Path.read_text = fake_read_text  # type: ignore[method-assign]
    try:
        yield
    finally:
        Path.is_file = real_is_file  # type: ignore[method-assign]
        Path.read_text = real_read_text  # type: ignore[method-assign]


@contextmanager
def _empty_auth_file():
    """An interrupted login: the auth file exists but carries no token."""
    real_is_file = Path.is_file
    real_read_text = Path.read_text

    def fake_is_file(self: Path) -> bool:
        return "nonexistent-auth" in str(self) or real_is_file(self)

    def fake_read_text(self: Path, *args, **kwargs) -> str:
        if "nonexistent-auth" in str(self):
            return "{}"
        return real_read_text(self, *args, **kwargs)

    Path.is_file = fake_is_file  # type: ignore[method-assign]
    Path.read_text = fake_read_text  # type: ignore[method-assign]
    try:
        yield
    finally:
        Path.is_file = real_is_file  # type: ignore[method-assign]
        Path.read_text = real_read_text  # type: ignore[method-assign]


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
            menu_message={"chat_id": 42, "message_id": 7},
        )
    finally:
        module.TaskRepository = original_repo

    assert "backup" not in text
    tasks_repo.create.assert_awaited_once()
    assert tasks_repo.create.await_args.kwargs["task_type"] == "cursor_account_login"
    payload = json.loads(tasks_repo.create.await_args.kwargs["payload"])
    assert payload["menu_message"] == {"chat_id": 42, "message_id": 7}
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
    service._accounts.get_account = MagicMock(return_value=MagicMock(id="backup"))  # type: ignore[method-assign]

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


@pytest.mark.asyncio
async def test_login_start_edits_menu_instead_of_new_message(test_settings) -> None:
    """Login link must land in the menu message, not a fresh message."""
    notifier = MagicMock()
    notifier.edit_live_message = AsyncMock()
    notifier.send_with_url_button = AsyncMock()
    service = CursorAccountLoginService(test_settings, MagicMock(), notifier)
    service.start_login = AsyncMock(
        return_value=LoginStartResult(
            account_id="main", login_url="https://cursor.com/x"
        )
    )

    await service.start_login_and_notify(
        42,
        "main",
        menu_message={"chat_id": 42, "message_id": 7},
    )

    notifier.edit_live_message.assert_awaited_once()
    args, kwargs = notifier.edit_live_message.await_args
    assert args[0] == 42
    assert args[1] == 7
    notifier.send_with_url_button.assert_not_awaited()

    markup = kwargs["reply_markup"]
    url_buttons = [
        btn for row in markup.inline_keyboard for btn in row if btn.url
    ]
    assert [btn.url for btn in url_buttons] == ["https://cursor.com/x"]


@pytest.mark.asyncio
async def test_login_start_falls_back_to_message_on_edit_failure(test_settings) -> None:
    """If the menu edit fails, the link must still reach the user."""
    notifier = MagicMock()
    notifier.edit_live_message = AsyncMock(side_effect=RuntimeError("boom"))
    notifier.send_with_url_button = AsyncMock()
    service = CursorAccountLoginService(test_settings, MagicMock(), notifier)
    service.start_login = AsyncMock(
        return_value=LoginStartResult(
            account_id="main", login_url="https://cursor.com/x"
        )
    )

    await service.start_login_and_notify(
        42,
        "main",
        menu_message={"chat_id": 42, "message_id": 7},
    )

    notifier.send_with_url_button.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_start_without_menu_message_sends_message(test_settings) -> None:
    """No menu reference (e.g. /account) keeps the old send behaviour."""
    notifier = MagicMock()
    notifier.edit_live_message = AsyncMock()
    notifier.send_with_url_button = AsyncMock()
    service = CursorAccountLoginService(test_settings, MagicMock(), notifier)
    service.start_login = AsyncMock(
        return_value=LoginStartResult(
            account_id="main", login_url="https://cursor.com/x"
        )
    )

    await service.start_login_and_notify(42, "main")

    notifier.send_with_url_button.assert_awaited_once()
    notifier.edit_live_message.assert_not_awaited()


def test_start_message_has_no_account_id(test_settings) -> None:
    """Single-account setup must not leak internal ids into user text."""
    service = CursorAccountLoginService(test_settings, MagicMock(), MagicMock())
    result = LoginStartResult(account_id="main", login_url="https://cursor.com/x")
    text = service.format_start_message(result)
    assert "main" not in text
    assert "https://cursor.com/x" not in text


@pytest.mark.asyncio
async def test_login_success_edits_menu_not_new_message(test_settings) -> None:
    """'Вход выполнен' must land in the menu message, not a fresh one."""
    notifier = MagicMock()
    notifier.edit_live_message = AsyncMock()
    notifier.send = AsyncMock()
    service = CursorAccountLoginService(test_settings, _redis_stub(), notifier)
    service._accounts.register_account = MagicMock(
        return_value=MagicMock(id="main", label="Основной")
    )
    service._accounts.fetch_usage = AsyncMock(
        return_value=MagicMock(plan_name="Pro")
    )
    service._accounts.set_active_account = AsyncMock()
    service._refresh_models_note = AsyncMock(return_value="Каталог загружен.")

    process = MagicMock()
    process.wait = AsyncMock(return_value=0)
    process.returncode = 0
    service._terminate_process = AsyncMock()

    with _fake_auth_file():
        await service._monitor_login(
            42,
            "main",
            process,
            Path("/tmp/nonexistent-auth.json"),
            menu_message={"chat_id": 42, "message_id": 7},
        )

    notifier.edit_live_message.assert_awaited_once()
    notifier.send.assert_not_awaited()
    args, _kwargs = notifier.edit_live_message.await_args
    assert args[0] == 42
    assert args[1] == 7
    assert "Вход в Cursor выполнен" in args[2]


@pytest.mark.asyncio
async def test_login_without_token_is_reported_as_failure(test_settings) -> None:
    """An auth.json with no token must not count as a completed login."""
    notifier = MagicMock()
    notifier.edit_live_message = AsyncMock()
    notifier.send = AsyncMock()
    service = CursorAccountLoginService(test_settings, _redis_stub(), notifier)
    service._accounts.register_account = MagicMock()
    service._terminate_process = AsyncMock()

    process = MagicMock()
    process.wait = AsyncMock(return_value=0)
    process.returncode = 0

    with _empty_auth_file():
        await service._monitor_login(
            42,
            "main",
            process,
            Path("/tmp/nonexistent-auth.json"),
            menu_message={"chat_id": 42, "message_id": 7},
        )

    service._accounts.register_account.assert_not_called()
    args, _kwargs = notifier.edit_live_message.await_args
    assert "Не удалось завершить вход" in args[2]


@pytest.mark.asyncio
async def test_login_timeout_edits_menu_not_new_message(test_settings) -> None:
    """Timeout must also stay inside the menu message."""
    notifier = MagicMock()
    notifier.edit_live_message = AsyncMock()
    notifier.send = AsyncMock()
    service = CursorAccountLoginService(test_settings, _redis_stub(), notifier)
    service._terminate_process = AsyncMock()

    process = MagicMock()
    process.wait = AsyncMock(side_effect=TimeoutError())

    await service._monitor_login(
        42,
        "main",
        process,
        Path("/tmp/nonexistent-auth.json"),
        menu_message={"chat_id": 42, "message_id": 7},
    )

    notifier.edit_live_message.assert_awaited_once()
    notifier.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_end_falls_back_to_message_on_edit_failure(test_settings) -> None:
    """If the menu edit fails, the outcome must still reach the user."""
    notifier = MagicMock()
    notifier.edit_live_message = AsyncMock(side_effect=RuntimeError("boom"))
    notifier.send = AsyncMock()
    service = CursorAccountLoginService(test_settings, MagicMock(), notifier)

    await service._publish_login_end(
        42,
        {"chat_id": 42, "message_id": 7},
        "Вход в Cursor выполнен.",
        logged_in=True,
    )

    notifier.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_login_end_without_menu_message_sends_message(test_settings) -> None:
    """No menu reference (e.g. /account) keeps the send behaviour."""
    notifier = MagicMock()
    notifier.edit_live_message = AsyncMock()
    notifier.send = AsyncMock()
    service = CursorAccountLoginService(test_settings, MagicMock(), notifier)

    await service._publish_login_end(
        42, None, "Вход в Cursor выполнен.", logged_in=True
    )

    notifier.send.assert_awaited_once()
    notifier.edit_live_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_login_end_uses_rich_markdown(test_settings) -> None:
    """Markdown must render, not show literal asterisks."""
    notifier = MagicMock()
    notifier.edit_live_message = AsyncMock()
    notifier.send = AsyncMock()
    service = CursorAccountLoginService(test_settings, MagicMock(), notifier)

    await service._publish_login_end(
        42,
        {"chat_id": 42, "message_id": 7},
        "Вход в Cursor выполнен.\nПлан: *Pro*",
        logged_in=True,
    )

    assert notifier.edit_live_message.await_args.kwargs["rich_markdown"] is True


@pytest.mark.asyncio
async def test_login_start_uses_rich_markdown(test_settings) -> None:
    notifier = MagicMock()
    notifier.edit_live_message = AsyncMock()
    notifier.send_with_url_button = AsyncMock()
    service = CursorAccountLoginService(test_settings, MagicMock(), notifier)
    service.start_login = AsyncMock(
        return_value=LoginStartResult(
            account_id="main", login_url="https://cursor.com/x"
        )
    )

    await service.start_login_and_notify(
        42, "main", menu_message={"chat_id": 42, "message_id": 7}
    )

    assert notifier.edit_live_message.await_args.kwargs["rich_markdown"] is True


@pytest.mark.asyncio
async def test_models_catalog_note_is_empty(test_settings) -> None:
    """Login UX must not mention the models catalog at all."""
    notifier = MagicMock()
    notifier.edit_live_message = AsyncMock()
    notifier.send = AsyncMock()
    service = CursorAccountLoginService(test_settings, _redis_stub(), notifier)
    service._accounts.register_account = MagicMock(
        return_value=MagicMock(id="main", label="Основной")
    )
    service._accounts.fetch_usage = AsyncMock(
        return_value=MagicMock(plan_name="Pro")
    )
    service._accounts.set_active_account = AsyncMock()

    process = MagicMock()
    process.wait = AsyncMock(return_value=0)
    process.returncode = 0

    with _fake_auth_file():
        await service._monitor_login(
            42,
            "main",
            process,
            Path("/tmp/nonexistent-auth.json"),
            menu_message={"chat_id": 42, "message_id": 7},
        )

    text = notifier.edit_live_message.await_args.args[2]
    assert "Вход в Cursor выполнен" in text
    assert "План: *Pro*" in text
    assert "Каталог" not in text
    assert "Модели" not in text


@pytest.mark.asyncio
async def test_queue_logout_carries_menu_message(test_settings) -> None:
    """Logout must edit the menu message, not announce it separately."""
    service = CursorAccountLoginService(test_settings, MagicMock(), MagicMock())

    db = MagicMock()
    db.commit = AsyncMock()
    task_queue = MagicMock()
    task_queue.enqueue = AsyncMock()

    created_task = MagicMock()
    created_task.id = "task-logout"
    tasks_repo = MagicMock()
    tasks_repo.create = AsyncMock(return_value=created_task)

    from telegram_cursor_agent.services import cursor_account_login as module

    original_repo = module.TaskRepository
    module.TaskRepository = MagicMock(return_value=tasks_repo)
    try:
        await service.queue_logout(
            db,
            task_queue,
            user_id="user-id",
            telegram_id=42,
            menu_message={"chat_id": 42, "message_id": 7},
        )
    finally:
        module.TaskRepository = original_repo

    payload = json.loads(tasks_repo.create.await_args.kwargs["payload"])
    assert payload["menu_message"] == {"chat_id": 42, "message_id": 7}
    assert payload["telegram_id"] == 42
