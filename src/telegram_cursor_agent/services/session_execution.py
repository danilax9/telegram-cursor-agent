"""Redis-backed session execution state and redirect coordination."""

from __future__ import annotations

import json
import uuid
from enum import StrEnum
from typing import Any

from redis.asyncio import Redis

from telegram_cursor_agent.core.logging import get_logger
from telegram_cursor_agent.queue.task_queue import SESSION_REDIRECT_CHANNEL

logger = get_logger(__name__)

SESSION_EXEC_STATE_KEY = "tca:session:{session_id}:exec_state"
SESSION_RUNNING_TASK_KEY = "tca:session:{session_id}:running_task"
SESSION_REDIRECT_PAYLOAD_KEY = "tca:session:{session_id}:redirect_payload"
SESSION_LIVE_MESSAGE_KEY = "tca:session:{session_id}:live_message"
SESSION_KEY_TTL_SECONDS = 86_400


class SessionExecState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    INTERRUPTING = "interrupting"
    REDIRECTING = "redirecting"


class SessionExecutionService:
    """Tracks per-session Cursor CLI lifecycle and pending redirects."""

    def __init__(self, redis: Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    async def get_state(self, session_id: uuid.UUID) -> SessionExecState:
        raw = await self._redis.get(self._state_key(session_id))
        if raw is None:
            return SessionExecState.IDLE
        value = raw.decode() if isinstance(raw, bytes) else str(raw)
        try:
            return SessionExecState(value)
        except ValueError:
            return SessionExecState.IDLE

    async def set_state(self, session_id: uuid.UUID, state: SessionExecState) -> None:
        key = self._state_key(session_id)
        await self._redis.set(key, state.value, ex=SESSION_KEY_TTL_SECONDS)

    async def register_running_task(
        self, session_id: uuid.UUID, task_id: uuid.UUID
    ) -> None:
        key = self._running_task_key(session_id)
        await self._redis.set(key, str(task_id), ex=SESSION_KEY_TTL_SECONDS)

    async def clear_running_task(self, session_id: uuid.UUID) -> None:
        await self._redis.delete(self._running_task_key(session_id))

    async def get_running_task_id(self, session_id: uuid.UUID) -> uuid.UUID | None:
        raw = await self._redis.get(self._running_task_key(session_id))
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        try:
            return uuid.UUID(text)
        except ValueError:
            return None

    async def set_pending_redirect(
        self, session_id: uuid.UUID, payload: dict[str, Any]
    ) -> bool:
        """Store redirect payload; returns True if a previous payload was replaced."""
        key = self._redirect_payload_key(session_id)
        replaced = await self._redis.exists(key) == 1
        if replaced:
            logger.info(
                "redirect_payload_replaced",
                session_id=str(session_id),
            )
        encoded = json.dumps(payload, ensure_ascii=False)
        await self._redis.set(key, encoded, ex=SESSION_KEY_TTL_SECONDS)
        return bool(replaced)

    async def peek_pending_redirect(self, session_id: uuid.UUID) -> dict[str, Any] | None:
        raw = await self._redis.get(self._redirect_payload_key(session_id))
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        parsed: Any = json.loads(text)
        return parsed if isinstance(parsed, dict) else None

    async def consume_pending_redirect(
        self, session_id: uuid.UUID
    ) -> dict[str, Any] | None:
        key = self._redirect_payload_key(session_id)
        raw = await self._redis.getdel(key)
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        parsed: Any = json.loads(text)
        return parsed if isinstance(parsed, dict) else None

    async def publish_redirect(self, session_id: uuid.UUID) -> None:
        await self._redis.publish(SESSION_REDIRECT_CHANNEL, str(session_id))

    async def set_live_message(
        self, session_id: uuid.UUID, telegram_id: int, message_id: int
    ) -> None:
        key = self._live_message_key(session_id)
        payload = json.dumps({"telegram_id": telegram_id, "message_id": message_id})
        await self._redis.set(key, payload, ex=SESSION_KEY_TTL_SECONDS)

    async def get_live_message(
        self, session_id: uuid.UUID
    ) -> tuple[int, int] | None:
        raw = await self._redis.get(self._live_message_key(session_id))
        if raw is None:
            return None
        text = raw.decode() if isinstance(raw, bytes) else str(raw)
        parsed: Any = json.loads(text)
        if not isinstance(parsed, dict):
            return None
        try:
            return int(parsed["telegram_id"]), int(parsed["message_id"])
        except (KeyError, TypeError, ValueError):
            return None

    async def clear_live_message(self, session_id: uuid.UUID) -> None:
        await self._redis.delete(self._live_message_key(session_id))

    @staticmethod
    def _state_key(session_id: uuid.UUID) -> str:
        return SESSION_EXEC_STATE_KEY.format(session_id=session_id)

    @staticmethod
    def _running_task_key(session_id: uuid.UUID) -> str:
        return SESSION_RUNNING_TASK_KEY.format(session_id=session_id)

    @staticmethod
    def _redirect_payload_key(session_id: uuid.UUID) -> str:
        return SESSION_REDIRECT_PAYLOAD_KEY.format(session_id=session_id)

    @staticmethod
    def _live_message_key(session_id: uuid.UUID) -> str:
        return SESSION_LIVE_MESSAGE_KEY.format(session_id=session_id)
