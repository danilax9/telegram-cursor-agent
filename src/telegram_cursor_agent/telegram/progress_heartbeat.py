"""Periodic live-message updates when the agent is silent for too long."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable


class ProgressHeartbeat:
    """Emit a heartbeat if no real progress update arrived within the interval."""

    def __init__(
        self,
        on_heartbeat: Callable[[str], Awaitable[None]],
        interval_seconds: float = 45.0,
    ) -> None:
        self._on_heartbeat = on_heartbeat
        self._interval = interval_seconds
        self._started_at = time.monotonic()
        self._last_activity = self._started_at
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    def touch(self) -> None:
        self._last_activity = time.monotonic()

    async def _loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(self._interval)
                idle = time.monotonic() - self._last_activity
                if idle < self._interval:
                    continue
                elapsed = int(time.monotonic() - self._started_at)
                mins, secs = divmod(elapsed, 60)
                if mins:
                    text = f"⏳ Выполняю... {mins}м {secs:02d}с"
                else:
                    text = f"⏳ Выполняю... {secs}с"
                await self._on_heartbeat(text)
        except asyncio.CancelledError:
            raise
