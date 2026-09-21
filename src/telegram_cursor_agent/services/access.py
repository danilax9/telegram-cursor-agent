"""Manage bot access for Telegram accounts."""

from __future__ import annotations

import re

from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import is_super_admin, require_super_admin
from telegram_cursor_agent.database.models.user import User
from telegram_cursor_agent.database.repositories.user import UserRepository
from telegram_cursor_agent.telegram.markdown import md_bold, md_code

_TELEGRAM_ID_RE = re.compile(r"^\d{5,20}$")


class AccessError(Exception):
    pass


class AccessService:
    def __init__(self, db: AsyncSession, settings: Settings) -> None:
        self._db = db
        self._settings = settings
        self._users = UserRepository(db)

    def ensure_can_manage(self, telegram_user_id: int) -> None:
        require_super_admin(telegram_user_id, self._settings)

    async def list_access(self) -> str:
        lines = [md_bold("Доступ к боту"), ""]
        lines.append("*Владельцы (из .env):*")
        for owner_id in self._settings.telegram_admin_ids:
            user = await self._users.get_by_telegram_id(owner_id)
            label = self._format_user(user, owner_id)
            lines.append(f"• {label} — нельзя удалить")
        lines.append("")
        lines.append("*Добавленные в чате:*")
        granted = [
            user
            for user in await self._users.list_authorized()
            if not is_super_admin(user.telegram_id, self._settings)
        ]
        if not granted:
            lines.append("• пока никого")
        else:
            for user in granted:
                lines.append(f"• {self._format_user(user, user.telegram_id)}")
        lines.extend([
            "",
            "Добавить:",
            "• `/access add 123456789`",
            "• `/access add @username` (если человек уже писал /start)",
            "• ответь `/access add` на сообщение пользователя",
            "",
            "Убрать: `/access remove 123456789`",
        ])
        return "\n".join(lines)

    async def grant(self, manager_id: int, target: str) -> str:
        self.ensure_can_manage(manager_id)
        telegram_id, username = await self._resolve_target(target)
        if is_super_admin(telegram_id, self._settings):
            return "Этот аккаунт уже владелец бота из .env."
        user = await self._users.grant_access(telegram_id, username)
        return f"✅ Доступ выдан: {self._format_user(user, telegram_id)}"

    async def grant_from_reply(
        self,
        manager_id: int,
        telegram_id: int,
        username: str | None,
    ) -> str:
        self.ensure_can_manage(manager_id)
        if is_super_admin(telegram_id, self._settings):
            return "Этот аккаунт уже владелец бота из .env."
        user = await self._users.grant_access(telegram_id, username)
        return f"✅ Доступ выдан: {self._format_user(user, telegram_id)}"

    async def revoke(self, manager_id: int, target: str) -> str:
        self.ensure_can_manage(manager_id)
        telegram_id, _ = await self._resolve_target(target, allow_username=False)
        if is_super_admin(telegram_id, self._settings):
            raise AccessError("Нельзя убрать владельца из .env.")
        user = await self._users.revoke_access(telegram_id)
        if user is None:
            raise AccessError(f"Пользователь `{telegram_id}` не найден.")
        return f"🚫 Доступ убран: {md_code(str(telegram_id))}"

    async def _resolve_target(
        self, target: str, *, allow_username: bool = True
    ) -> tuple[int, str | None]:
        value = target.strip()
        if not value:
            raise AccessError(
                "Укажи ID или @username.\nПример: `/access add 123456789`"
            )
        if value.startswith("@") and allow_username:
            user = await self._users.get_by_username(value)
            if user is None:
                raise AccessError(
                    f"Пользователь {value} не найден. "
                    "Попроси его написать /start или укажи numeric ID."
                )
            return user.telegram_id, user.username
        if _TELEGRAM_ID_RE.match(value):
            return int(value), None
        raise AccessError(
            "Нужен numeric Telegram ID (5–20 цифр) или @username из базы."
        )

    @staticmethod
    def _format_user(user: User | None, telegram_id: int) -> str:
        if user and user.username:
            return f"@{user.username} ({md_code(str(telegram_id))})"
        return md_code(str(telegram_id))
