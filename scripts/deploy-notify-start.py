#!/usr/bin/env python3
"""Send the pre-deploy Telegram warning before worker restart."""

from __future__ import annotations

import asyncio

from telegram_cursor_agent.core.config import get_settings
from telegram_cursor_agent.database.session import create_engine, create_session_factory
from telegram_cursor_agent.services.deploy_context_snapshot import (
    ensure_marker,
    snapshot_running_deploy_context,
)
from telegram_cursor_agent.services.deploy_resume import (
    DEPLOY_START_WARNING,
    claim_deploy_start_notification,
    read_marker,
)
from telegram_cursor_agent.telegram.notifier import TelegramNotifier


async def main() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    marker = read_marker(settings)
    if marker is None or marker.telegram_id is None:
        context = await snapshot_running_deploy_context(session_factory, settings)
        if context is not None:
            ensure_marker(settings, context)
            marker = read_marker(settings)

    telegram_id = claim_deploy_start_notification(settings)
    if telegram_id is None:
        return

    notifier = TelegramNotifier(settings)
    try:
        await notifier.send(telegram_id, DEPLOY_START_WARNING)
    finally:
        await notifier.close()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
