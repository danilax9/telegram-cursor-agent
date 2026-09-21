"""Redis-backed task queue."""

from collections.abc import AsyncIterator

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.exceptions import TimeoutError as RedisTimeoutError

from telegram_cursor_agent.core.config import Settings

TASK_QUEUE_KEY = "tca:tasks"
TASK_NOTIFY_CHANNEL = "tca:task_notify"
TASK_CANCEL_CHANNEL = "tca:task_cancel"


class TaskQueue:
    def __init__(self, redis_client: Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis_client

    async def enqueue(self, task_id: str) -> None:
        await self._redis.rpush(TASK_QUEUE_KEY, task_id)
        await self._redis.publish(TASK_NOTIFY_CHANNEL, task_id)

    async def dequeue(self, block_seconds: int = 5) -> str | None:
        try:
            result = await self._redis.blpop(TASK_QUEUE_KEY, timeout=block_seconds)
        except RedisTimeoutError:
            # A blocking pop timing out means the queue is empty, not that the worker failed.
            return None
        if result is None:
            return None
        _, task_id = result
        return task_id.decode() if isinstance(task_id, bytes) else str(task_id)

    async def length(self) -> int:
        return await self._redis.llen(TASK_QUEUE_KEY)

    async def publish_cancel(self, task_id: str) -> None:
        await self._redis.publish(TASK_CANCEL_CHANNEL, task_id)

    async def listen_cancel(self) -> AsyncIterator[str]:
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(TASK_CANCEL_CHANNEL)
        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                task_id = message["data"]
                yield task_id.decode() if isinstance(task_id, bytes) else str(task_id)
        finally:
            await pubsub.unsubscribe(TASK_CANCEL_CHANNEL)
            await pubsub.close()


async def create_redis(settings: Settings) -> Redis:  # type: ignore[type-arg]
    # Health checks plus keepalive let a long-idle worker notice a dropped
    # connection and reconnect instead of failing the next dequeue.
    return aioredis.from_url(
        settings.redis_url,
        decode_responses=False,
        health_check_interval=30,
        socket_keepalive=True,
        retry_on_timeout=True,
    )
