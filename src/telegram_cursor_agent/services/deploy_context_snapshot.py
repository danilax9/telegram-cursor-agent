"""Capture deploy context from the running worker task or DB."""

from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.database.models.task import Task
from telegram_cursor_agent.database.models.user import User
from telegram_cursor_agent.database.repositories.session import SessionRepository
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.services.deploy_resume import (
    DeployContext,
    read_marker,
    update_marker_fields,
    write_marker,
)


async def refresh_deploy_context_from_db(
    session_factory: async_sessionmaker[AsyncSession],
    context: DeployContext,
    settings: Settings,
) -> DeployContext:
    """Fill cursor_chat_id and workspace from DB when the on-disk marker is stale."""
    if context.session_id is None:
        return context

    async with session_factory() as db:
        agent_session = await SessionRepository(db).get_by_id(context.session_id)
        if agent_session is None:
            return context
        cursor_chat_id = agent_session.cursor_chat_id or context.cursor_chat_id
        workspace = settings.normalize_cursor_workspace(
            agent_session.workspace_path or context.workspace
        )
        if context.user_id is None:
            user = await UserRepository(db).get_by_id(agent_session.user_id)
            telegram_id = user.telegram_id if user else context.telegram_id
        else:
            telegram_id = context.telegram_id

    return DeployContext(
        task_id=context.task_id,
        user_id=context.user_id or agent_session.user_id,
        telegram_id=telegram_id,
        session_id=context.session_id,
        cursor_chat_id=cursor_chat_id,
        workspace=workspace,
    )


async def snapshot_running_deploy_context(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> DeployContext | None:
    """Find the active agent/deploy task to preserve Telegram + Cursor session."""
    async with session_factory() as db:
        result = await db.execute(
            select(Task, User)
            .join(User, User.id == Task.user_id)
            .where(Task.status == "running")
            .where(Task.task_type.in_(("agent_prompt", "deploy")))
            .order_by(Task.started_at.desc())
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        task, user = row

        cursor_chat_id = None
        workspace = None
        if task.session_id:
            agent_session = await SessionRepository(db).get_by_id(task.session_id)
            if agent_session:
                cursor_chat_id = agent_session.cursor_chat_id
                workspace = agent_session.workspace_path

        if not workspace and task.payload:
            try:
                payload = json.loads(task.payload)
            except json.JSONDecodeError:
                payload = {}
            workspace = payload.get("agent_workspace") or payload.get("workspace")

        return DeployContext(
            task_id=task.id,
            user_id=task.user_id,
            telegram_id=user.telegram_id,
            session_id=task.session_id,
            cursor_chat_id=cursor_chat_id,
            workspace=settings.normalize_cursor_workspace(workspace)
            if workspace
            else None,
        )


def ensure_marker(settings: Settings, context: DeployContext) -> None:
    marker = read_marker(settings)
    if marker is not None and marker.telegram_id is not None:
        update_marker_fields(
            settings,
            cursor_chat_id=context.cursor_chat_id,
            session_id=str(context.session_id) if context.session_id else None,
            workspace=context.workspace,
            task_id=str(context.task_id) if context.task_id else None,
            user_id=str(context.user_id) if context.user_id else None,
        )
        return
    write_marker(settings, context, status="started")
