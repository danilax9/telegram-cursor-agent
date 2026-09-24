"""Liveness signals read by the deploy guard (scripts/tca_guard.py).

Key names and JSON fields are a contract with the guard script, which reads
them with a stdlib-only Redis client. Change both sides together.
"""

from __future__ import annotations

import asyncio
import json
import os
import time

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from redis.asyncio import Redis

from telegram_cursor_agent.core.logging import get_logger

logger = get_logger(__name__)

HEALTH_KEY_PREFIX = "tca:health"
HEARTBEAT_INTERVAL_SECONDS = 10
HEARTBEAT_TTL_SECONDS = 90
RESULT_TTL_SECONDS = 3600

WORKER_COMPONENT = "worker"
BOT_COMPONENT = "bot"


def heartbeat_key(component: str) -> str:
    return f"{HEALTH_KEY_PREFIX}:{component}"


def errors_key(component: str) -> str:
    return f"{HEALTH_KEY_PREFIX}:{component}:errors"


def resume_result_key(deploy_id: str) -> str:
    return f"{HEALTH_KEY_PREFIX}:resume:{deploy_id}"


def notify_result_key(deploy_id: str) -> str:
    return f"{HEALTH_KEY_PREFIX}:notify:{deploy_id}"


def is_transient_delivery_error(exc: BaseException) -> bool:
    """Telegram outages and flood limits are not bugs in the deployed code."""
    return isinstance(
        exc, (TelegramNetworkError, TelegramRetryAfter, TimeoutError, ConnectionError)
    )


async def write_heartbeat(
    redis: Redis,  # type: ignore[type-arg]
    component: str,
    *,
    revision: str,
    started_at: float,
) -> None:
    payload = {
        "pid": os.getpid(),
        "revision": revision,
        "started_at": started_at,
        "ts": time.time(),
    }
    await redis.set(heartbeat_key(component), json.dumps(payload), ex=HEARTBEAT_TTL_SECONDS)


async def run_heartbeat(
    redis: Redis,  # type: ignore[type-arg]
    component: str,
    *,
    revision: str,
    started_at: float,
    interval: float = HEARTBEAT_INTERVAL_SECONDS,
) -> None:
    """Runs as its own task so a long agent turn does not look like a hang."""
    while True:
        try:
            await write_heartbeat(
                redis, component, revision=revision, started_at=started_at
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("heartbeat_write_failed", component=component, exc_info=True)
        await asyncio.sleep(interval)


async def record_error(
    redis: Redis | None,  # type: ignore[type-arg]
    component: str,
) -> None:
    if redis is None:
        return
    try:
        await redis.incr(errors_key(component))
    except Exception:
        logger.warning("health_error_record_failed", component=component, exc_info=True)


async def record_json(
    redis: Redis | None,  # type: ignore[type-arg]
    key: str,
    payload: dict[str, object],
) -> None:
    if redis is None:
        return
    try:
        await redis.set(key, json.dumps(payload, ensure_ascii=False), ex=RESULT_TTL_SECONDS)
    except Exception:
        logger.warning("health_result_record_failed", key=key, exc_info=True)
