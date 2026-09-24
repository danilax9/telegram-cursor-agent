"""Worker reliability: no lost results, no orphaned tasks, no zombie processes."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.queue.worker import STUCK_TASK_MESSAGE, TaskWorker
from telegram_cursor_agent.services.agent_errors import AgentTimeoutError


@pytest.fixture
def worker_settings(test_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("SELF_REPO_ROOT", str(tmp_path))
    from telegram_cursor_agent.core.config import clear_settings_cache, get_settings

    clear_settings_cache()
    return get_settings()


def _mock_notifier():
    notifier = MagicMock()
    notifier.send = AsyncMock()
    notifier.keep_typing = AsyncMock()
    notifier.send_typing_once = AsyncMock()
    notifier.send_live_start = AsyncMock(return_value=1)
    notifier.edit_live_message = AsyncMock()
    notifier.close = AsyncMock()
    return notifier


def _make_worker(settings, session_factory, notifier):
    """Build a worker without touching Telegram or a real engine."""
    worker = object.__new__(TaskWorker)
    worker._settings = settings
    worker._engine = None
    worker._session_factory = session_factory
    worker._runner = MagicMock()
    worker._redis = MagicMock()
    worker._queue = None
    worker._notifier = notifier
    worker._active_task_pids = {}
    worker._current_task_id = None
    worker._last_watchdog_at = 0.0
    return worker


async def _pending_task(db_session, *, telegram_id: int, payload: str, task_type="agent_prompt"):
    user = await UserRepository(db_session).upsert(
        telegram_id=telegram_id, username=f"u{telegram_id}", is_admin=True
    )
    task = await TaskRepository(db_session).create(
        user_id=user.id, task_type=task_type, payload=payload
    )
    await db_session.commit()
    return user, task


async def test_delivery_failure_keeps_completed_result(
    db_session, worker_settings, tmp_path
) -> None:
    """A Telegram outage must not discard a finished Cursor answer."""
    _user, task = await _pending_task(
        db_session,
        telegram_id=9001,
        payload=json.dumps({"prompt": "work", "interrupt_recovery": True}),
    )
    notifier = _mock_notifier()
    notifier.send = AsyncMock(side_effect=RuntimeError("telegram down"))
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    worker = _make_worker(worker_settings, session_factory, notifier)
    worker._execute = AsyncMock(return_value="the answer")

    await worker._process_task(str(task.id))

    async with session_factory() as check:
        stored = await TaskRepository(check).get_by_id(task.id)
        assert stored is not None
        assert stored.status == "completed"
        assert stored.result == "the answer"


async def test_broken_payload_fails_task_instead_of_hanging(
    db_session, worker_settings
) -> None:
    """Any error outside the agent run must still end the task and warn the user."""
    _user, task = await _pending_task(db_session, telegram_id=9002, payload="{not json")
    notifier = _mock_notifier()
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    worker = _make_worker(worker_settings, session_factory, notifier)

    await worker._process_task(str(task.id))

    async with session_factory() as check:
        stored = await TaskRepository(check).get_by_id(task.id)
        assert stored is not None
        assert stored.status == "failed"
    notifier.send.assert_awaited_once()
    assert "прервалась" in notifier.send.await_args.args[1]


async def test_status_write_retries_after_db_blip(db_session, worker_settings) -> None:
    """A transient database error must not leave the task running forever."""
    _user, task = await _pending_task(
        db_session,
        telegram_id=9003,
        payload=json.dumps({"prompt": "work", "interrupt_recovery": True}),
    )
    real_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    calls = {"count": 0}

    def flaky_factory():
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("connection reset")
        return real_factory()

    notifier = _mock_notifier()
    worker = _make_worker(worker_settings, flaky_factory, notifier)
    worker._execute = AsyncMock(return_value="answer")

    await worker._process_task(str(task.id))

    async with real_factory() as check:
        stored = await TaskRepository(check).get_by_id(task.id)
        assert stored is not None
        assert stored.status == "completed"


async def test_watchdog_reclaims_abandoned_running_task(
    db_session, worker_settings
) -> None:
    """A task left running by a dead worker is failed and reported."""
    user = await UserRepository(db_session).upsert(
        telegram_id=9004, username="stuck", is_admin=True
    )
    tasks = TaskRepository(db_session)
    task = await tasks.create(
        user_id=user.id, task_type="agent_prompt", payload=json.dumps({"prompt": "p"})
    )
    await tasks.mark_running(task.id)
    task.started_at = datetime.now(UTC) - timedelta(hours=10)
    await db_session.commit()

    notifier = _mock_notifier()
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    worker = _make_worker(worker_settings, session_factory, notifier)

    await worker._watchdog_stuck_tasks()

    async with session_factory() as check:
        stored = await TaskRepository(check).get_by_id(task.id)
        assert stored is not None
        assert stored.status == "failed"
    notifier.send.assert_awaited_once_with(9004, STUCK_TASK_MESSAGE, reply_markup=None)


async def test_watchdog_leaves_fresh_and_current_tasks_alone(
    db_session, worker_settings
) -> None:
    user = await UserRepository(db_session).upsert(
        telegram_id=9005, username="fresh", is_admin=True
    )
    tasks = TaskRepository(db_session)
    fresh = await tasks.create(
        user_id=user.id, task_type="agent_prompt", payload=json.dumps({"prompt": "p"})
    )
    current = await tasks.create(
        user_id=user.id, task_type="agent_prompt", payload=json.dumps({"prompt": "p"})
    )
    await tasks.mark_running(fresh.id)
    await tasks.mark_running(current.id)
    current.started_at = datetime.now(UTC) - timedelta(hours=10)
    await db_session.commit()

    notifier = _mock_notifier()
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    worker = _make_worker(worker_settings, session_factory, notifier)
    worker._current_task_id = str(current.id)

    await worker._watchdog_stuck_tasks()

    async with session_factory() as check:
        repo = TaskRepository(check)
        assert (await repo.get_by_id(fresh.id)).status == "running"
        assert (await repo.get_by_id(current.id)).status == "running"
    notifier.send.assert_not_awaited()


async def test_watchdog_is_throttled(db_session, worker_settings) -> None:
    notifier = _mock_notifier()
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    worker = _make_worker(worker_settings, session_factory, notifier)

    await worker._watchdog_stuck_tasks()
    first_run = worker._last_watchdog_at
    await worker._watchdog_stuck_tasks()

    assert worker._last_watchdog_at == first_run


async def test_live_finalize_failure_falls_back_to_plain_message(
    worker_settings,
) -> None:
    notifier = _mock_notifier()
    worker = _make_worker(worker_settings, MagicMock(), notifier)
    live = MagicMock()
    live.message_id = 42
    live.finalize = AsyncMock(side_effect=RuntimeError("edit failed"))

    await worker._deliver_result(live, 9006, "result text")

    notifier.send.assert_awaited_once_with(9006, "result text", reply_markup=None)


async def test_cancel_listener_is_restarted_after_failure(worker_settings) -> None:
    """A dropped Redis pubsub must not silently disable /cancel."""
    notifier = _mock_notifier()
    worker = _make_worker(worker_settings, MagicMock(), notifier)
    attempts = {"count": 0}

    async def failing_listener() -> None:
        attempts["count"] += 1
        raise RuntimeError("pubsub dropped")

    worker._listen_cancellations = failing_listener
    supervisor = asyncio.create_task(worker._supervise_cancellations())
    await asyncio.sleep(0.05)
    supervisor.cancel()
    await asyncio.gather(supervisor, return_exceptions=True)

    assert attempts["count"] >= 1


async def test_timeout_is_reported_as_timeout_not_cancellation(
    db_session, worker_settings
) -> None:
    """A task killed by the time limit must not masquerade as a user cancel."""
    _user, task = await _pending_task(
        db_session,
        telegram_id=9007,
        payload=json.dumps({"prompt": "work", "interrupt_recovery": True}),
    )
    notifier = _mock_notifier()
    session_factory = async_sessionmaker(bind=db_session.bind, expire_on_commit=False)
    settings = worker_settings.model_copy(update={"telegram_message_format": "legacy"})
    worker = _make_worker(settings, session_factory, notifier)
    worker._execute = AsyncMock(
        side_effect=AgentTimeoutError(1800, "переписал config.py")
    )

    await worker._process_task(str(task.id))

    async with session_factory() as check:
        stored = await TaskRepository(check).get_by_id(task.id)
        assert stored is not None
        assert stored.status == "failed"
        assert "timed out" in (stored.error or "")

    notifier.send.assert_awaited()
    message = notifier.send.await_args.args[1]
    assert "Задача отменена" not in message
    assert "30 мин" in message
    assert "переписал config.py" in message
    assert "/resume" in message


async def test_timeout_keeps_partial_output_and_chat_id(runner: ProcessRunner) -> None:
    """Streamed work collected before the kill must survive the timeout."""
    script = (
        'printf \'{"type":"assistant","chat_id":"chat-77",'
        '"message":{"content":[{"text":"partial work"}]}}\\n\'; sleep 30'
    )
    result = await runner.run(["sh", "-c", script], timeout_seconds=1.0)

    assert result.timed_out is True
    assert result.cancelled is False
    assert "partial work" in result.stdout
    assert "chat-77" in result.stdout


async def test_user_cancel_is_not_reported_as_timeout(runner: ProcessRunner) -> None:
    process_started: asyncio.Future[int] = asyncio.get_running_loop().create_future()

    async def on_start(pid: int) -> None:
        process_started.set_result(pid)

    run_task = asyncio.create_task(
        runner.run(["sleep", "30"], timeout_seconds=30, on_process_start=on_start)
    )
    pid = await asyncio.wait_for(process_started, timeout=5)
    await runner.cancel(pid)
    result = await asyncio.wait_for(run_task, timeout=10)

    assert result.cancelled is True
    assert result.timed_out is False


async def test_timed_out_process_is_reaped(runner: ProcessRunner) -> None:
    """A killed agent must not linger as a zombie."""
    process = await asyncio.create_subprocess_exec(
        "sleep", "30", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    process.kill()

    await ProcessRunner._reap(process)

    assert process.returncode is not None
