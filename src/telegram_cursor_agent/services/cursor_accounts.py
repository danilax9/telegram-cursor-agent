"""Cursor multi-account registry, activation, and auto-rotation."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from redis.asyncio import Redis

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.services.usage import (
    CursorUsageError,
    CursorUsageService,
    CursorUsageSnapshot,
)

ACTIVE_ACCOUNT_KEY = "tca:active_cursor_account"
EXHAUSTED_ACCOUNT_PREFIX = "tca:cursor_account_exhausted:"


@dataclass(frozen=True)
class CursorAccount:
    id: str
    label: str
    auth_file: Path
    priority: int = 0


@dataclass
class CursorAccountsConfig:
    accounts: list[CursorAccount]
    auto_rotate: bool = True
    usage_threshold_percent: float = 95.0


class CursorAccountError(Exception):
    pass


class CursorAccountService:
    def __init__(self, settings: Settings, redis_client: Redis | None = None) -> None:  # type: ignore[type-arg]
        self._settings = settings
        self._redis = redis_client

    def load_config(self) -> CursorAccountsConfig:
        accounts_file = self._settings.cursor_accounts_file
        if accounts_file.is_file():
            raw = json.loads(accounts_file.read_text(encoding="utf-8"))
            accounts = [
                CursorAccount(
                    id=str(item["id"]),
                    label=str(item.get("label") or item["id"]),
                    auth_file=Path(item["auth_file"]).expanduser(),
                    priority=int(item.get("priority", 0)),
                )
                for item in raw.get("accounts", [])
            ]
            if not accounts:
                raise CursorAccountError("accounts.json is empty.")
            return CursorAccountsConfig(
                accounts=sorted(accounts, key=lambda account: account.priority),
                auto_rotate=bool(raw.get("auto_rotate", True)),
                usage_threshold_percent=float(raw.get("usage_threshold_percent", 95.0)),
            )

        default = CursorAccount(
            id="default",
            label="Default",
            auth_file=self._settings.cursor_auth_file,
            priority=0,
        )
        return CursorAccountsConfig(accounts=[default])

    def list_accounts(self) -> list[CursorAccount]:
        return self.load_config().accounts

    async def get_active_account(self) -> CursorAccount:
        accounts = self.list_accounts()
        active_id = await self._get_active_account_id()
        if active_id:
            for account in accounts:
                if account.id == active_id:
                    return account
        return accounts[0]

    async def set_active_account(self, account_id: str) -> CursorAccount:
        account = self.get_account(account_id)
        await self._set_active_account_id(account.id)
        self.activate_account_files(account)
        return account

    def get_account(self, account_id: str) -> CursorAccount:
        for account in self.list_accounts():
            if account.id == account_id:
                return account
        raise CursorAccountError(f"Unknown account: {account_id}")

    def activate_account_files(self, account: CursorAccount) -> None:
        if not account.auth_file.is_file():
            raise CursorAccountError(
                f"Auth file missing for `{account.id}`: {account.auth_file}"
            )
        target = self._settings.cursor_auth_file
        if account.auth_file.resolve() == target.resolve():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(account.auth_file, target)

    async def activate_account(self, account: CursorAccount) -> None:
        self.activate_account_files(account)
        await self._set_active_account_id(account.id)

    def account_env(self, account: CursorAccount) -> dict[str, str]:
        env: dict[str, str] = {}
        home = account.auth_file.parent.parent.parent
        if (home / ".config" / "cursor" / "auth.json").resolve() == account.auth_file.resolve():
            env["HOME"] = str(home)
        return env

    async def fetch_usage(self, account: CursorAccount | None = None) -> CursorUsageSnapshot:
        account = account or await self.get_active_account()
        service = CursorUsageService(self._settings)
        return await service.fetch_usage(auth_file=account.auth_file)

    def is_exhausted(self, snapshot: CursorUsageSnapshot, threshold: float) -> bool:
        if snapshot.total_percent_used >= threshold:
            return True
        message = (snapshot.display_message or "").lower()
        exhausted_markers = (
            "limit reached",
            "out of usage",
            "subscription ended",
            "subscription expired",
            "trial ended",
        )
        return any(marker in message for marker in exhausted_markers)

    async def ensure_healthy_active_account(self) -> CursorAccount | None:
        """Switch away from exhausted or invalid active account when possible."""
        config = self.load_config()
        if not config.auto_rotate or len(config.accounts) < 2:
            return None

        active = await self.get_active_account()
        if await self._is_marked_exhausted(active.id):
            return await self._rotate_from(active.id, reason="exhausted")

        try:
            snapshot = await self.fetch_usage(active)
        except CursorUsageError:
            return await self._rotate_from(active.id, reason="auth")

        if self.is_exhausted(snapshot, config.usage_threshold_percent):
            await self._mark_exhausted(active.id, snapshot.billing_cycle_end.timestamp())
            return await self._rotate_from(active.id, reason="limit")

        return None

    async def rotate_after_failure(
        self, failed_account_id: str, reason: str
    ) -> CursorAccount | None:
        config = self.load_config()
        if not config.auto_rotate or len(config.accounts) < 2:
            return None
        if reason in {"auth", "limit"}:
            await self._mark_exhausted(failed_account_id)
        return await self._rotate_from(failed_account_id, reason=reason)

    def register_account(
        self,
        account_id: str,
        auth_file: Path,
        label: str | None = None,
    ) -> CursorAccount:
        config = self.load_config()
        existing = next(
            (account for account in config.accounts if account.id == account_id),
            None,
        )
        priority = existing.priority if existing is not None else self._next_priority(config)
        account = CursorAccount(
            id=account_id,
            label=label or (existing.label if existing is not None else account_id),
            auth_file=auth_file,
            priority=priority,
        )
        others = [item for item in config.accounts if item.id != account_id]
        others.append(account)
        self._write_config(
            CursorAccountsConfig(
                accounts=sorted(others, key=lambda item: item.priority),
                auto_rotate=config.auto_rotate,
                usage_threshold_percent=config.usage_threshold_percent,
            )
        )
        return account

    def _write_config(self, config: CursorAccountsConfig) -> None:
        payload = {
            "auto_rotate": config.auto_rotate,
            "usage_threshold_percent": config.usage_threshold_percent,
            "accounts": [
                {
                    "id": account.id,
                    "label": account.label,
                    "auth_file": str(account.auth_file),
                    "priority": account.priority,
                }
                for account in sorted(config.accounts, key=lambda item: item.priority)
            ],
        }
        path = self._settings.cursor_accounts_file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _next_priority(config: CursorAccountsConfig) -> int:
        if not config.accounts:
            return 0
        return max(account.priority for account in config.accounts) + 1

    async def format_accounts_message(self) -> str:
        active = await self.get_active_account()
        lines = ["*Аккаунты Cursor*", ""]
        for account in self.list_accounts():
            marker = "✓ " if account.id == active.id else "• "
            status = ""
            try:
                snapshot = await self.fetch_usage(account)
                status = f" — {round(snapshot.total_percent_used, 1)}% ({snapshot.plan_name})"
            except CursorUsageError as exc:
                status = f" — _{exc}_"
            except Exception:
                status = " — _не удалось получить лимиты_"
            lines.append(f"{marker}*{account.id}* ({account.label}){status}")
        lines.extend(
            [
                "",
                f"Активный: `{active.id}`",
                "",
                "Команды:",
                "• `/account use <id>` — переключить",
                "• `/account limits` — лимиты всех аккаунтов",
                "• `/account add <id>` — добавить через браузер",
                "• `/account cancel` — отменить вход",
            ]
        )
        return "\n".join(lines)

    def prepare_account_directory(self, account_id: str) -> Path:
        base = self._settings.cursor_accounts_dir / account_id
        auth_dir = base / ".config" / "cursor"
        auth_dir.mkdir(parents=True, exist_ok=True)
        return base

    def format_prepare_message(self, account_id: str) -> str:
        home = self.prepare_account_directory(account_id)
        auth_file = home / ".config" / "cursor" / "auth.json"
        return (
            f"Подготовлена директория для аккаунта `{account_id}`.\n\n"
            "На сервере выполни:\n"
            f"`HOME={home} {self._settings.cursor_agent_bin} login`\n\n"
            f"Затем добавь аккаунт в `{self._settings.cursor_accounts_file}` "
            f"с auth_file `{auth_file}`.\n\n"
            f"После этого: `/account use {account_id}`"
        )

    async def _rotate_from(self, failed_account_id: str, reason: str) -> CursorAccount | None:
        accounts = self.list_accounts()
        start_index = next(
            (index for index, account in enumerate(accounts) if account.id == failed_account_id),
            -1,
        )
        for account in accounts[start_index + 1 :] + accounts[:start_index]:
            if account.id == failed_account_id:
                continue
            if await self._is_marked_exhausted(account.id):
                continue
            if not account.auth_file.is_file():
                continue
            try:
                snapshot = await self.fetch_usage(account)
            except CursorUsageError:
                continue
            config = self.load_config()
            if self.is_exhausted(snapshot, config.usage_threshold_percent):
                await self._mark_exhausted(account.id, snapshot.billing_cycle_end.timestamp())
                continue
            await self.activate_account(account)
            return account
        return None

    async def _get_active_account_id(self) -> str | None:
        if self._redis is None:
            return None
        raw = await self._redis.get(ACTIVE_ACCOUNT_KEY)
        return raw.decode() if isinstance(raw, bytes) else raw

    async def _set_active_account_id(self, account_id: str) -> None:
        if self._redis is None:
            return
        await self._redis.set(ACTIVE_ACCOUNT_KEY, account_id)

    async def _mark_exhausted(self, account_id: str, until_ts: float | None = None) -> None:
        if self._redis is None:
            return
        ttl = 3600
        if until_ts is not None:
            from time import time

            ttl = max(int(until_ts - time()), 3600)
        await self._redis.set(f"{EXHAUSTED_ACCOUNT_PREFIX}{account_id}", "1", ex=ttl)

    async def _is_marked_exhausted(self, account_id: str) -> bool:
        if self._redis is None:
            return False
        return bool(await self._redis.get(f"{EXHAUSTED_ACCOUNT_PREFIX}{account_id}"))
