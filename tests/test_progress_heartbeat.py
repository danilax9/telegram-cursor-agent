"""Progress heartbeat tests."""

import asyncio
from unittest.mock import AsyncMock

from telegram_cursor_agent.telegram.progress_heartbeat import ProgressHeartbeat


async def test_emits_heartbeat_after_interval() -> None:
    on_heartbeat = AsyncMock()
    heartbeat = ProgressHeartbeat(on_heartbeat, interval_seconds=0.05)
    await heartbeat.start()
    await asyncio.sleep(0.12)
    await heartbeat.stop()
    assert on_heartbeat.await_count >= 1


async def test_touch_resets_idle_timer() -> None:
    on_heartbeat = AsyncMock()
    heartbeat = ProgressHeartbeat(on_heartbeat, interval_seconds=0.2)
    await heartbeat.start()
    for _ in range(4):
        heartbeat.touch()
        await asyncio.sleep(0.05)
    await heartbeat.stop()
    assert on_heartbeat.await_count == 0
