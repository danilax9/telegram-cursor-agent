"""Ожидание следующего сообщения для пункта меню (имя сессии, доступ)."""

from __future__ import annotations

import json
from typing import Any

from redis.asyncio import Redis

MENU_INPUT_PREFIX = "tca:menu_input:"
MENU_INPUT_TTL_SECONDS = 600


def _key(telegram_id: int) -> str:
    return f"{MENU_INPUT_PREFIX}{telegram_id}"


async def set_menu_input(
    redis_client: Redis,  # type: ignore[type-arg]
    telegram_id: int,
    payload: dict[str, Any],
) -> None:
    await redis_client.set(
        _key(telegram_id),
        json.dumps(payload, ensure_ascii=False),
        ex=MENU_INPUT_TTL_SECONDS,
    )


async def peek_menu_input(
    redis_client: Redis,  # type: ignore[type-arg]
    telegram_id: int,
) -> dict[str, Any] | None:
    raw = await redis_client.get(_key(telegram_id))
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    data = json.loads(raw)
    return data if isinstance(data, dict) else None


async def clear_menu_input(
    redis_client: Redis,  # type: ignore[type-arg]
    telegram_id: int,
) -> None:
    await redis_client.delete(_key(telegram_id))
