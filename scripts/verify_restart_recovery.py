"""Live end-to-end check of restart recovery against real Postgres + Redis.

Runs against a throwaway database so production tasks are never touched.
"""

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

REPO = Path(__file__).resolve().parent.parent
TEST_DB = "tca_recovery_verify"
DB_URL = f"postgresql+asyncpg://tca:tca_secret@127.0.0.1:5433/{TEST_DB}"

os.environ["DATABASE_URL"] = DB_URL
os.environ["REDIS_URL"] = "redis://127.0.0.1:6380/15"
os.environ["SELF_DEPLOY_ENABLED"] = "true"
os.environ["SELF_REPO_ROOT"] = "/tmp/tca-verify-repo"
Path("/tmp/tca-verify-repo").mkdir(parents=True, exist_ok=True)

from telegram_cursor_agent.core.config import clear_settings_cache, get_settings  # noqa: E402
from telegram_cursor_agent.database.base import Base  # noqa: E402
from telegram_cursor_agent.database.repositories.session import SessionRepository  # noqa: E402
from telegram_cursor_agent.database.repositories.task import TaskRepository  # noqa: E402
from telegram_cursor_agent.database.repositories.user import UserRepository  # noqa: E402
from telegram_cursor_agent.services.deploy_recovery import DeployRecoveryService  # noqa: E402
from telegram_cursor_agent.services.deploy_resume import (  # noqa: E402
    DEPLOY_SUCCESS_MESSAGE,
    MAX_RECOVERY_ATTEMPTS,
    RECOVERY_ATTEMPT_KEY,
    DeployContext,
    clear_marker,
    read_marker,
    write_marker,
)

failures: list[str] = []


async def reset_schema(engine) -> None:
    """Postgres has a users<->projects FK cycle, so drop the schema wholesale."""
    from sqlalchemy import text

    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
        await conn.run_sync(Base.metadata.create_all)


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {name}")
    else:
        failures.append(f"{name}: {detail}")
        print(f"  FAIL  {name} {detail}")


async def seed_running_task(factory, telegram_id: int, payload_extra=None, chat="chat-1"):
    async with factory() as db:
        user = await UserRepository(db).upsert(
            telegram_id=telegram_id, username=f"u{telegram_id}", is_admin=True
        )
        session = await SessionRepository(db).create(
            user_id=user.id, workspace_path="/tmp", cursor_chat_id=chat
        )
        payload = {"prompt": "work", "agent_workspace": "/tmp"}
        payload.update(payload_extra or {})
        tasks = TaskRepository(db)
        task = await tasks.create(
            user_id=user.id,
            task_type="agent_prompt",
            payload=json.dumps(payload),
            session_id=session.id,
        )
        await tasks.mark_running(task.id)
        await db.commit()
        return user, session, task


def make_service(settings, factory, notifier, redis):
    return DeployRecoveryService(settings, factory, notifier=notifier, redis=redis)


async def main() -> int:
    from telegram_cursor_agent.queue.task_queue import create_redis

    clear_settings_cache()
    settings = get_settings()
    engine = create_async_engine(settings.database_url)
    await reset_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    redis = await create_redis(settings)

    print("\nScenario 1+3: unexpected restart (manual/crash) with running agent_prompt")
    clear_marker(settings)
    user, session, task = await seed_running_task(factory, 900001)
    write_marker(
        settings,
        DeployContext(
            task_id=task.id,
            user_id=user.id,
            telegram_id=user.telegram_id,
            session_id=session.id,
            cursor_chat_id="chat-1",
            workspace="/tmp",
        ),
        status="session_active",
    )
    notifier = MagicMock()
    notifier.send = AsyncMock()
    await make_service(settings, factory, notifier, redis).recover_tasks_on_startup()

    check("user notified", notifier.send.await_count >= 1,
          f"awaited={notifier.send.await_count}")
    async with factory() as db:
        repo = TaskRepository(db)
        refreshed = await repo.get_by_id(task.id)
        check("interrupted task not running", refreshed.status != "running",
              f"status={refreshed.status}")
        pending = await repo.list_pending(limit=10)
        check("session auto-resume queued", len(pending) == 1, f"pending={len(pending)}")
        if pending:
            payload = json.loads(pending[0].payload or "{}")
            check("resume marked as recovery", payload.get("interrupt_recovery") is True)
            check("attempt counter set", payload.get(RECOVERY_ATTEMPT_KEY) == 1,
                  f"attempt={payload.get(RECOVERY_ATTEMPT_KEY)}")
    check("stale session_active marker cleared", read_marker(settings) is None)

    print("\nScenario 2: deploy restart hands notifications to delivery")
    clear_marker(settings)
    await reset_schema(engine)
    user, session, task = await seed_running_task(factory, 900002)
    write_marker(
        settings,
        DeployContext(
            task_id=task.id,
            user_id=user.id,
            telegram_id=user.telegram_id,
            session_id=session.id,
            cursor_chat_id="chat-1",
            workspace="/tmp",
        ),
        status="pending_resume",
    )
    notifier = MagicMock()
    notifier.send = AsyncMock()
    service = make_service(settings, factory, notifier, redis)
    await service.recover_tasks_on_startup()
    marker = read_marker(settings)
    check("marker holds notifications", bool(marker and marker.notifications))
    check("tasks_recovered flag set", bool(marker and marker.tasks_recovered))
    async with factory() as db:
        pending = await TaskRepository(db).list_pending(limit=10)
        check("deploy auto-resume queued", len(pending) == 1, f"pending={len(pending)}")
        if pending:
            payload = json.loads(pending[0].payload or "{}")
            check("resume flagged deploy_recovery",
                  payload.get("deploy_recovery") is True)

    await service.deliver_stored_notifications()
    check("deploy confirmation delivered", notifier.send.await_count >= 1,
          f"awaited={notifier.send.await_count}")
    check("marker cleared after delivery", read_marker(settings) is None)

    print("\nScenario 4: second delivery attempt must not duplicate or hang")
    notifier2 = MagicMock()
    notifier2.send = AsyncMock()
    service2 = make_service(settings, factory, notifier2, redis)
    await service2.deliver_stored_notifications()
    check("no duplicate message", notifier2.send.await_count == 0,
          f"awaited={notifier2.send.await_count}")
    claim = Path(settings.self_repo_root) / ".deploy-pending.claimed.json"
    check("no leftover claim file", not claim.exists())

    print("\nScenario 4b: restart loop is capped")
    clear_marker(settings)
    await reset_schema(engine)
    _u, _s, looped = await seed_running_task(
        factory,
        900003,
        payload_extra={
            "interrupt_recovery": True,
            RECOVERY_ATTEMPT_KEY: MAX_RECOVERY_ATTEMPTS,
        },
    )
    notifier3 = MagicMock()
    notifier3.send = AsyncMock()
    await make_service(settings, factory, notifier3, redis).recover_tasks_on_startup()
    async with factory() as db:
        repo = TaskRepository(db)
        check("exhausted task failed",
              (await repo.get_by_id(looped.id)).status == "failed")
        check("no endless resume queued",
              len(await repo.list_pending(limit=10)) == 0)
    check("user told to retry manually", notifier3.send.await_count == 1,
          f"awaited={notifier3.send.await_count}")

    print("\nScenario: many interrupted tasks all drained")
    clear_marker(settings)
    await reset_schema(engine)
    async with factory() as db:
        user = await UserRepository(db).upsert(
            telegram_id=900004, username="many", is_admin=True
        )
        tasks = TaskRepository(db)
        for i in range(25):
            t = await tasks.create(
                user_id=user.id, task_type="agent_prompt",
                payload=json.dumps({"prompt": f"p{i}"}),
            )
            await tasks.mark_running(t.id)
        await db.commit()
    notifier4 = MagicMock()
    notifier4.send = AsyncMock()
    await make_service(settings, factory, notifier4, redis).recover_tasks_on_startup()
    async with factory() as db:
        still = await TaskRepository(db).list_running(limit=None)
        check("all 25 drained from running", len(still) == 0, f"still_running={len(still)}")

    clear_marker(settings)
    await redis.flushdb()
    await redis.aclose()
    await engine.dispose()

    print()
    if failures:
        print(f"FAILURES ({len(failures)}):")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("ALL LIVE RECOVERY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
