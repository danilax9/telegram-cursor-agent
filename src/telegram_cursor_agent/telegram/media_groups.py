"""Collect Telegram media-group messages before processing albums."""

import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable

from aiogram.types import Message

MediaGroupHandler = Callable[[list[Message]], Awaitable[None]]

_album_buffers: dict[str, list[Message]] = defaultdict(list)
_album_tasks: dict[str, asyncio.Task[None]] = {}
ALBUM_COLLECT_DELAY_SECONDS = 0.6


async def schedule_media_group(
    message: Message,
    handler: MediaGroupHandler,
    delay_seconds: float = ALBUM_COLLECT_DELAY_SECONDS,
) -> None:
    """Buffer album parts and invoke handler once after a short delay."""
    if message.media_group_id is None:
        await handler([message])
        return

    group_id = message.media_group_id
    _album_buffers[group_id].append(message)

    existing = _album_tasks.get(group_id)
    if existing is not None:
        existing.cancel()

    async def _flush() -> None:
        await asyncio.sleep(delay_seconds)
        messages = _album_buffers.pop(group_id, [])
        _album_tasks.pop(group_id, None)
        if messages:
            await handler(messages)

    _album_tasks[group_id] = asyncio.create_task(_flush())
