"""Persist deploy context across worker restarts and resume Cursor sessions."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from telegram_cursor_agent.core.config import Settings

MARKER_VERSION = 1
MARKER_FILENAME = ".deploy-pending.json"

DEPLOY_START_WARNING = "🔄 Перезапуск бота ~10 секунд"

DEPLOY_SUCCESS_MESSAGE = "🟢 Бот онлайн после перезапуска"

FIX_REQUEST_FILENAME = ".deploy-fix-request.json"

STUCK_TYPING_MESSAGE = (
    "⚠️ Ответ не дошёл: я печатал, но сервис перезапустился. "
    "Смотрю последний диалог и пришлю отчёт."
)

ROLLBACK_DIAGNOSIS_PROMPT = (
    "[System: A self-update failed and the last good snapshot was restored. "
    "The user saw a typing status, but the reply never arrived. "
    "Diagnose from this same Cursor chat — the last dialogue in the session. "
    "Then send one Russian Telegram report: what they asked, why the answer did not arrive, "
    "what broke, what was restored, and that they can continue. "
    "Do not deploy again until tests pass. Do not paste this system block to the user.]"
)


def build_rollback_diagnosis_prompt(reason: str) -> str:
    detail = (reason or "").strip()
    if not detail:
        return ROLLBACK_DIAGNOSIS_PROMPT
    return f"{ROLLBACK_DIAGNOSIS_PROMPT}\n\nСбой:\n{detail[-3000:]}"


def fix_request_path(settings: Settings) -> Path:
    return settings.self_repo_root / FIX_REQUEST_FILENAME


def write_fix_request(
    settings: Settings,
    *,
    reason: str,
    telegram_id: int | None,
    user_id: str | None,
    session_id: str | None,
    cursor_chat_id: str | None,
    workspace: str | None,
) -> None:
    payload = {
        "reason": reason[-4000:],
        "telegram_id": telegram_id,
        "user_id": user_id,
        "session_id": session_id,
        "cursor_chat_id": cursor_chat_id,
        "workspace": workspace,
        "created_at": datetime.now(UTC).isoformat(),
    }
    fix_request_path(settings).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def claim_fix_request(settings: Settings) -> dict[str, object] | None:
    path = fix_request_path(settings)
    claimed = path.with_suffix(".claimed.json")
    try:
        path.rename(claimed)
    except (FileNotFoundError, OSError):
        return None
    try:
        raw = json.loads(claimed.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        claimed.unlink(missing_ok=True)
        return None
    claimed.unlink(missing_ok=True)
    return raw if isinstance(raw, dict) else None


DEPLOY_RESUME_PROMPT = (
    "[System: Self-deploy finished successfully. The bot and worker have restarted. "
    "Send the user a concise Telegram Markdown reply confirming the update is complete, "
    "what changed in this session, and that they can continue chatting normally.]"
)

SESSION_ACTIVE_STATUS = "session_active"
DEPLOY_PENDING_STATUS = "deploy_pending"
DEPLOY_RECOVERY_STATUSES = frozenset({"pending_resume", DEPLOY_PENDING_STATUS})

INTERRUPTED_RECOVERY_MESSAGE = (
    "⚠️ Сервис перезапустился. Продолжаю с того же места — можно писать дальше."
)

INTERRUPTED_RESUME_PROMPT = (
    "[System: The worker restarted while a previous turn was still running. "
    "Send the user a concise Telegram Markdown reply: acknowledge the restart briefly, "
    "finish any incomplete answer from the prior turn if still useful, and confirm "
    "they can continue chatting normally. "
    "Do not paste this system block to the user.]"
)


def build_interrupt_recovery_prompt(original_prompt: str | None) -> str:
    """Resume the same Cursor chat and continue the user's interrupted task."""
    user_part = (original_prompt or "").strip()
    if user_part.startswith("[System:"):
        user_part = ""
    if not user_part:
        return INTERRUPTED_RESUME_PROMPT
    return (
        f"{INTERRUPTED_RESUME_PROMPT}\n\n"
        "Прерванная инструкция пользователя (продолжи с `--resume`, "
        "контекст чата должен сохраниться):\n"
        f"{user_part}"
    )

NO_SESSION_RECOVERY_MESSAGE = (
    "⚠️ Предыдущая задача прервалась при перезапуске. "
    "Отправь сообщение снова или /resume."
)

RECOVERY_EXHAUSTED_MESSAGE = (
    "⚠️ Не удалось автоматически продолжить после нескольких перезапусков. "
    "Напиши сообщение заново — сессия сохранена, можно продолжать с того же места."
)

# Guards against a crash loop: a resume that dies on its own stops being
# re-queued. A deliberate restart (systemctl, self-deploy) is not a crash and
# must not spend this budget — a second deploy in one session is normal.
RECOVERY_ATTEMPT_KEY = "recovery_attempt"
MAX_RECOVERY_ATTEMPTS = 2
CLEAN_SHUTDOWN_FILENAME = ".worker-clean-shutdown"


@dataclass
class DeployContext:
    task_id: uuid.UUID | None = None
    user_id: uuid.UUID | None = None
    telegram_id: int | None = None
    session_id: uuid.UUID | None = None
    cursor_chat_id: str | None = None
    workspace: str | None = None


@dataclass
class DeployMarker:
    version: int
    status: str
    task_id: str | None = None
    user_id: str | None = None
    telegram_id: int | None = None
    session_id: str | None = None
    cursor_chat_id: str | None = None
    workspace: str | None = None
    deploy_log: str | None = None
    created_at: str | None = None
    completed_at: str | None = None
    notifications: list[dict[str, object]] | None = None
    tasks_recovered: bool = False
    recovery_token: str | None = None
    deploy_notified: bool = False

    def to_context(self) -> DeployContext:
        return DeployContext(
            task_id=uuid.UUID(self.task_id) if self.task_id else None,
            user_id=uuid.UUID(self.user_id) if self.user_id else None,
            telegram_id=self.telegram_id,
            session_id=uuid.UUID(self.session_id) if self.session_id else None,
            cursor_chat_id=self.cursor_chat_id,
            workspace=self.workspace,
        )


def marker_path(settings: Settings) -> Path:
    return settings.self_repo_root / MARKER_FILENAME


def clean_shutdown_path(settings: Settings) -> Path:
    return settings.self_repo_root / CLEAN_SHUTDOWN_FILENAME


def mark_clean_shutdown(settings: Settings) -> None:
    """Record that this process is stopping on purpose (SIGTERM), not crashing."""
    path = clean_shutdown_path(settings)
    path.write_text(
        json.dumps(
            {"pid": os.getpid(), "at": datetime.now(UTC).isoformat()},
            ensure_ascii=False,
        )
    )


def consume_clean_shutdown(settings: Settings) -> bool:
    """Return True once if the previous worker stopped on purpose."""
    path = clean_shutdown_path(settings)
    if not path.is_file():
        return False
    try:
        path.unlink()
    except OSError:
        return False
    return True


def write_marker(settings: Settings, context: DeployContext, *, status: str = "started") -> Path:
    path = marker_path(settings)
    payload = {
        "version": MARKER_VERSION,
        "status": status,
        "task_id": str(context.task_id) if context.task_id else None,
        "user_id": str(context.user_id) if context.user_id else None,
        "telegram_id": context.telegram_id,
        "session_id": str(context.session_id) if context.session_id else None,
        "cursor_chat_id": context.cursor_chat_id,
        "workspace": context.workspace,
        "deploy_log": None,
        "created_at": datetime.now(UTC).isoformat(),
        "completed_at": None,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    return path


def read_marker(settings: Settings) -> DeployMarker | None:
    path = marker_path(settings)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("version") != MARKER_VERSION:
        return None
    return _marker_from_raw(raw)


def _marker_from_raw(raw: dict) -> DeployMarker:
    return DeployMarker(
        version=MARKER_VERSION,
        status=str(raw.get("status", "")),
        task_id=raw.get("task_id"),
        user_id=raw.get("user_id"),
        telegram_id=raw.get("telegram_id"),
        session_id=raw.get("session_id"),
        cursor_chat_id=raw.get("cursor_chat_id"),
        workspace=raw.get("workspace"),
        deploy_log=raw.get("deploy_log"),
        created_at=raw.get("created_at"),
        completed_at=raw.get("completed_at"),
        notifications=raw.get("notifications"),
        tasks_recovered=bool(raw.get("tasks_recovered")),
        recovery_token=raw.get("recovery_token"),
        deploy_notified=bool(raw.get("deploy_notified")),
    )


def should_recover_deploy(marker: DeployMarker) -> bool:
    if marker.tasks_recovered:
        return False
    if marker.status in DEPLOY_RECOVERY_STATUSES:
        return True
    return marker.deploy_notified and marker.status == DEPLOY_PENDING_STATUS


def store_recovery_state(
    settings: Settings,
    *,
    notifications: list[dict[str, object]],
) -> None:
    path = marker_path(settings)
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict):
        return
    raw["notifications"] = notifications
    raw["tasks_recovered"] = True
    if not raw.get("recovery_token"):
        raw["recovery_token"] = str(uuid.uuid4())
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))


def clear_marker(settings: Settings) -> None:
    path = marker_path(settings)
    if path.is_file():
        path.unlink()


def claim_marker_for_delivery(settings: Settings) -> DeployMarker | None:
    """Atomically take ownership of pending notifications.

    Worker and bot both try to deliver after a restart. A POSIX rename is atomic,
    so exactly one process wins the claim and the user never gets duplicates.
    """
    path = marker_path(settings)
    claim_path = path.with_suffix(".claimed.json")
    try:
        path.rename(claim_path)
    except (FileNotFoundError, OSError):
        return None

    try:
        raw = json.loads(claim_path.read_text())
    except (OSError, json.JSONDecodeError):
        release_claim(settings)
        return None
    if not isinstance(raw, dict) or raw.get("version") != MARKER_VERSION:
        release_claim(settings)
        return None
    return _marker_from_raw(raw)


def release_claim(settings: Settings) -> None:
    claim_path = marker_path(settings).with_suffix(".claimed.json")
    if claim_path.is_file():
        claim_path.unlink()


def mark_deploy_notified(settings: Settings) -> None:
    path = marker_path(settings)
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict):
        return
    raw["status"] = DEPLOY_PENDING_STATUS
    raw["deploy_notified"] = True
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))


def claim_deploy_start_notification(settings: Settings) -> int | None:
    """Reserve the right to send the deploy warning exactly once.

    Returns the Telegram chat id when this caller should send, else None.
    """
    path = marker_path(settings)
    if not path.is_file():
        return None
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or raw.get("deploy_notified"):
        return None
    telegram_id = raw.get("telegram_id")
    if telegram_id is None:
        return None
    raw["deploy_notified"] = True
    raw["status"] = DEPLOY_PENDING_STATUS
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))
    return int(telegram_id)


def update_marker_completed(settings: Settings) -> None:
    path = marker_path(settings)
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict):
        return
    raw["status"] = "pending_resume"
    raw["completed_at"] = datetime.now(UTC).isoformat()
    raw.pop("deploy_log", None)
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))


def update_marker_fields(
    settings: Settings,
    *,
    cursor_chat_id: str | None = None,
    session_id: str | None = None,
    workspace: str | None = None,
    task_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Merge fresh session fields into the deploy marker without resetting status."""
    path = marker_path(settings)
    if not path.is_file():
        return
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(raw, dict) or raw.get("version") != MARKER_VERSION:
        return
    if cursor_chat_id:
        raw["cursor_chat_id"] = cursor_chat_id
    if session_id:
        raw["session_id"] = session_id
    if workspace:
        raw["workspace"] = workspace
    if task_id:
        raw["task_id"] = task_id
    if user_id:
        raw["user_id"] = user_id
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2))


def context_from_dict(data: dict[str, object]) -> DeployContext:
    def _uuid(value: object) -> uuid.UUID | None:
        if value is None:
            return None
        return uuid.UUID(str(value))

    return DeployContext(
        task_id=_uuid(data.get("task_id")),
        user_id=_uuid(data.get("user_id")),
        telegram_id=int(data["telegram_id"]) if data.get("telegram_id") is not None else None,
        session_id=_uuid(data.get("session_id")),
        cursor_chat_id=str(data["cursor_chat_id"]) if data.get("cursor_chat_id") else None,
        workspace=str(data["workspace"]) if data.get("workspace") else None,
    )


def context_to_env(context: DeployContext) -> dict[str, str]:
    env: dict[str, str] = {}
    mapping = {
        "DEPLOY_TASK_ID": context.task_id,
        "DEPLOY_USER_ID": context.user_id,
        "DEPLOY_SESSION_ID": context.session_id,
        "DEPLOY_CURSOR_CHAT_ID": context.cursor_chat_id,
        "DEPLOY_WORKSPACE": context.workspace,
        "DEPLOY_TELEGRAM_ID": context.telegram_id,
    }
    for key, value in mapping.items():
        if value is not None:
            env[key] = str(value)
    return env


def context_as_dict(context: DeployContext) -> dict[str, object]:
    return {key: str(value) if isinstance(value, uuid.UUID) else value for key, value in asdict(context).items()}
