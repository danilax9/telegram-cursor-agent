"""Redis-backed session execution state and redirect coordination.

A session is running only while the worker process that claimed it is still
alive. A bare task id left behind by SIGTERM is not a reason to redirect:
that path has no consumer and drops the user's message.
"""

from __future__ import annotations

import json
import os
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Any

from redis.asyncio import Redis

from telegram_cursor_agent.core.logging import get_logger
from telegram_cursor_agent.queue.task_queue import SESSION_REDIRECT_CHANNEL

logger = get_logger(__name__)

SESSION_EXEC_STATE_KEY = "tca:session:{session_id}:exec_state"
SESSION_RUNNING_TASK_KEY = "tca:session:{session_id}:running_task"
SESSION_RUNNING_OWNER_KEY = "tca:session:{session_id}:running_owner"
SESSION_REDIRECT_PAYLOAD_KEY = "tca:session:{session_id}:redirect_payload"
SESSION_LIVE_MESSAGE_KEY = "tca:session:{session_id}:live_message"
WORKER_OWNER_KEY = "tca:worker:owner"
SESSION_KEY_TTL_SECONDS = 86_400
_BUSY_STATES = frozenset(
    {
        "running",
        "interrupting",
        "redirecting",
    }
)


class SessionExecState(StrEnum):
    IDLE = "idle"
    RUNNING = "running"
    INTERRUPTING = "interrupting"
    REDIRECTING = "redirecting"


def worker_process_alive(pid: int) -> bool:
    """True when pid is this process or still the worker binary."""
    if pid <= 1:
        return False
    if pid == os.getpid():
        return True
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return False
    text = raw.replace(b"\x00", b" ").decode(errors="ignore")
    return (
        "telegram-cursor-worker" in text
        or "telegram_cursor_agent.queue.worker" in text
    )


class SessionExecutionService:
    """Tracks per-session Cursor CLI lifecycle and pending redirects."""

    def __init__(self, redis: Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    async def publish_owner(self) -> str:
        """Bind execution leases to this worker process."""
        epoch = str(uuid.uuid4())
        payload = json.dumps({"epoch": epoch, "pid": os.getpid()})
        await self._redis.set(WORKER_OWNER_KEY, payload)
        return epoch

    async def clear_owner(self) -> None:
        """Drop the worker epoch only if this process still owns it."""
        owner = await self._read_worker_owner()
        if owner is None or _pid_of(owner) != os.getpid():
            return
        await self._redis.delete(WORKER_OWNER_KEY)

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
        owner = await self._read_worker_owner()
        if owner is None or _pid_of(owner) != os.getpid():
            await self.publish_owner()
            owner = await self._read_worker_owner()
        if owner is None:
            return
        lease = json.dumps(
            {
                "epoch": owner.get("epoch"),
                "pid": os.getpid(),
                "task_id": str(task_id),
            }
        )
        await self._redis.set(
            self._running_task_key(session_id),
            str(task_id),
            ex=SESSION_KEY_TTL_SECONDS,
        )
        await self._redis.set(
            self._running_owner_key(session_id),
            lease,
            ex=SESSION_KEY_TTL_SECONDS,
        )

    async def clear_running_task(self, session_id: uuid.UUID) -> None:
        await self._redis.delete(self._running_task_key(session_id))
        await self._redis.delete(self._running_owner_key(session_id))

    async def release_if_owner(self, session_id: uuid.UUID, pid: int) -> None:
        """Clear the lease only when this process still owns it.

        A dying worker must not delete the lease a replacement worker already
        registered for the same session.
        """
        owner = _parse_obj(await self._redis.get(self._running_owner_key(session_id)))
        if owner is not None and _pid_of(owner) != pid:
            return
        if owner is not None:
            await self._delete_lease_if_unchanged(session_id, owner)
        await self._relax_busy_state(session_id)

    async def abandon_session(self, session_id: uuid.UUID) -> None:
        """Drop flags left by a worker that is no longer serving this turn."""
        await self.clear_running_task(session_id)
        await self._relax_busy_state(session_id)

    async def get_running_task_id(self, session_id: uuid.UUID) -> uuid.UUID | None:
        """Return the task id only when its worker process is still alive."""
        raw = await self._redis.get(self._running_task_key(session_id))
        if raw is None:
            return None
        text = _decode(raw)
        if text is None:
            return None
        try:
            task_id = uuid.UUID(text)
        except ValueError:
            await self.clear_running_task(session_id)
            await self._relax_busy_state(session_id)
            return None
        owner = _parse_obj(await self._redis.get(self._running_owner_key(session_id)))
        if not await self._lease_is_live(owner, task_id):
            await self._delete_lease_if_unchanged(session_id, owner)
            await self._relax_busy_state(session_id)
            return None
        return task_id

    async def list_redirect_session_ids(self) -> list[uuid.UUID]:
        """Sessions with a redirect payload nobody has consumed yet."""
        found: list[uuid.UUID] = []
        prefix = "tca:session:"
        suffix = ":redirect_payload"
        async for key in self._redis.scan_iter(
            match="tca:session:*:redirect_payload",
            count=100,
        ):
            text = _decode(key)
            if text is None or not text.startswith(prefix) or not text.endswith(suffix):
                continue
            raw_id = text[len(prefix) : -len(suffix)]
            try:
                found.append(uuid.UUID(raw_id))
            except ValueError:
                continue
        return found

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

    async def _lease_is_live(
        self, owner: dict[str, Any] | None, task_id: uuid.UUID
    ) -> bool:
        if owner is None:
            return False
        if str(owner.get("task_id", "")) != str(task_id):
            return False
        worker = await self._read_worker_owner()
        if worker is None:
            return False
        if str(owner.get("epoch")) != str(worker.get("epoch")):
            return False
        lease_pid = _pid_of(owner)
        if lease_pid is None or lease_pid != _pid_of(worker):
            return False
        return worker_process_alive(lease_pid)

    async def _delete_lease_if_unchanged(
        self, session_id: uuid.UUID, expected: dict[str, Any] | None
    ) -> None:
        current = _parse_obj(await self._redis.get(self._running_owner_key(session_id)))
        if expected is None:
            if current is not None:
                return
            await self._redis.delete(self._running_task_key(session_id))
            return
        if current is None:
            return
        if str(current.get("epoch")) != str(expected.get("epoch")):
            return
        if _pid_of(current) != _pid_of(expected):
            return
        await self.clear_running_task(session_id)

    async def _relax_busy_state(self, session_id: uuid.UUID) -> None:
        if await self._redis.get(self._running_owner_key(session_id)) is not None:
            return
        state = await self.get_state(session_id)
        if state.value in _BUSY_STATES:
            await self.set_state(session_id, SessionExecState.IDLE)

    async def _read_worker_owner(self) -> dict[str, Any] | None:
        return _parse_obj(await self._redis.get(WORKER_OWNER_KEY))

    @staticmethod
    def _state_key(session_id: uuid.UUID) -> str:
        return SESSION_EXEC_STATE_KEY.format(session_id=session_id)

    @staticmethod
    def _running_task_key(session_id: uuid.UUID) -> str:
        return SESSION_RUNNING_TASK_KEY.format(session_id=session_id)

    @staticmethod
    def _running_owner_key(session_id: uuid.UUID) -> str:
        return SESSION_RUNNING_OWNER_KEY.format(session_id=session_id)

    @staticmethod
    def _redirect_payload_key(session_id: uuid.UUID) -> str:
        return SESSION_REDIRECT_PAYLOAD_KEY.format(session_id=session_id)

    @staticmethod
    def _live_message_key(session_id: uuid.UUID) -> str:
        return SESSION_LIVE_MESSAGE_KEY.format(session_id=session_id)


def _decode(raw: object) -> str | None:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode()
    return str(raw)


def _parse_obj(raw: object) -> dict[str, Any] | None:
    text = _decode(raw)
    if not text:
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _pid_of(payload: dict[str, Any]) -> int | None:
    try:
        return int(payload.get("pid"))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
