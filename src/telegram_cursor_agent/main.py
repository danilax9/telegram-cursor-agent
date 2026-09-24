"""Application entry point."""

import asyncio
import time

from aiogram.types import ErrorEvent

from telegram_cursor_agent.core.config import get_settings
from telegram_cursor_agent.core.logging import get_logger, setup_logging
from telegram_cursor_agent.database.session import create_engine, create_session_factory
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.queue.task_queue import TaskQueue, create_redis
from telegram_cursor_agent.services.deploy_recovery import DeployRecoveryService
from telegram_cursor_agent.services.health import (
    BOT_COMPONENT,
    is_transient_delivery_error,
    record_error,
    run_heartbeat,
)
from telegram_cursor_agent.services.runtime_revision import source_revision
from telegram_cursor_agent.telegram.bot import create_bot, create_dispatcher
from telegram_cursor_agent.telegram.commands_menu import setup_bot_commands
from telegram_cursor_agent.telegram.notifier import TelegramNotifier

logger = get_logger(__name__)


async def _deliver_deploy_notifications(
    settings,
    session_factory,
    redis_client,
) -> None:
    notifier = TelegramNotifier(settings)
    try:
        recovery = DeployRecoveryService(
            settings,
            session_factory,
            notifier=notifier,
            redis=redis_client,
        )
        await recovery.deliver_notifications_when_ready()
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("deploy_notification_delivery_failed")
    finally:
        await notifier.close()


async def run() -> None:
    settings = get_settings()
    setup_logging(settings)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis_client = await create_redis(settings)
    task_queue = TaskQueue(redis_client)
    runner = ProcessRunner(settings)

    bot = create_bot(settings)
    dp = create_dispatcher(settings, session_factory, task_queue, runner, redis_client)

    logger.info("bot_starting", env=settings.app_env)
    try:
        await setup_bot_commands(bot)
    except Exception:
        # A transient Telegram error here must not crash-loop the container.
        logger.exception("setup_bot_commands_failed")
    started_at = time.time()

    @dp.errors()
    async def _on_update_error(event: ErrorEvent) -> bool:
        exc = event.exception
        if not is_transient_delivery_error(exc):
            await record_error(redis_client, BOT_COMPONENT)
        logger.exception("bot_update_failed", error=str(exc))
        return True

    notify_task = asyncio.create_task(
        _deliver_deploy_notifications(settings, session_factory, redis_client)
    )
    heartbeat = asyncio.create_task(
        run_heartbeat(
            redis_client,
            BOT_COMPONENT,
            revision=source_revision(),
            started_at=started_at,
        )
    )
    try:
        await dp.start_polling(bot)
    finally:
        notify_task.cancel()
        heartbeat.cancel()
        await asyncio.gather(notify_task, heartbeat, return_exceptions=True)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
