"""Application entry point."""

import asyncio

from telegram_cursor_agent.core.config import get_settings
from telegram_cursor_agent.core.logging import get_logger, setup_logging
from telegram_cursor_agent.database.session import create_engine, create_session_factory
from telegram_cursor_agent.execution.runner import ProcessRunner
from telegram_cursor_agent.queue.task_queue import TaskQueue, create_redis
from telegram_cursor_agent.telegram.bot import create_bot, create_dispatcher
from telegram_cursor_agent.telegram.commands_menu import setup_bot_commands

logger = get_logger(__name__)


async def run() -> None:
    settings = get_settings()
    setup_logging(settings)

    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    redis_client = await create_redis(settings)
    task_queue = TaskQueue(redis_client)
    runner = ProcessRunner(settings)

    bot = create_bot(settings)
    dp = create_dispatcher(settings, session_factory, task_queue, runner)

    logger.info("bot_starting", env=settings.app_env)
    await setup_bot_commands(bot)
    await dp.start_polling(bot)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
