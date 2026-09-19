"""Formatting helpers for agent session UI."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from telegram_cursor_agent.database.models.session import AgentSession


def short_session_id(session_id: UUID) -> str:
    return str(session_id)[:8]


def short_chat_id(cursor_chat_id: str | None) -> str:
    if not cursor_chat_id:
        return "—"
    return cursor_chat_id[:8]


def workspace_label(workspace_path: str) -> str:
    return Path(workspace_path).name or workspace_path


def format_last_active(last_active_at: datetime | None) -> str:
    if last_active_at is None:
        return "никогда"
    now = datetime.now(tz=UTC)
    delta = now - last_active_at.astimezone(UTC)
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return "только что"
    if seconds < 3600:
        return f"{seconds // 60} мин назад"
    if seconds < 86400:
        return f"{seconds // 3600} ч назад"
    return last_active_at.astimezone(UTC).strftime("%d.%m.%Y %H:%M")


def format_session_line(
    session: AgentSession,
    *,
    index: int | None = None,
    mark_active: bool = False,
) -> str:
    prefix = f"{index}. " if index is not None else ""
    active_marker = "● " if mark_active or session.status == "active" else ""
    status = ""
    if session.status == "archived":
        status = " (архив)"
    elif session.status == "deleted":
        status = " (удалена)"
    return (
        f"{prefix}{active_marker}`{short_session_id(session.id)}` — "
        f"{workspace_label(session.workspace_path)} — "
        f"chat `{short_chat_id(session.cursor_chat_id)}` — "
        f"{format_last_active(session.last_active_at)}{status}"
    )


def format_session_list(sessions: list[AgentSession], active_id: UUID | None) -> str:
    if not sessions:
        return "Нет сохранённых сессий. Отправь сообщение агенту или используй /new."
    lines = ["**Сессии Cursor:**", ""]
    for index, session in enumerate(sessions, start=1):
        lines.append(
            format_session_line(
                session,
                index=index,
                mark_active=active_id is not None and session.id == active_id,
            )
        )
    return "\n".join(lines)
