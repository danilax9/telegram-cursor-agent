"""In-bot Cursor account login via browser deep link."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.logging import get_logger
from telegram_cursor_agent.database.repositories.task import TaskRepository
from telegram_cursor_agent.queue.task_queue import TaskQueue
from telegram_cursor_agent.services.cursor_accounts import CursorAccountService
from telegram_cursor_agent.services.usage import CursorUsageError
from telegram_cursor_agent.telegram.notifier import TelegramNotifier

CURSOR_LOGIN_TASK_NOTIFIED = "__cursor_login_notified__"

LOGIN_URL_PATTERN = re.compile(r"https://cursor\.com/loginDeepControl\?[^\s\]]+")
ACCOUNT_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
LOGIN_SESSION_KEY_PREFIX = "tca:cursor_login:"
LOGIN_READ_TIMEOUT_SECONDS = 30

logger = get_logger(__name__)


class CursorAccountLoginError(Exception):
    pass


@dataclass(frozen=True)
class LoginStartResult:
    account_id: str
    login_url: str


class CursorAccountLoginService:
    _monitor_tasks: dict[int, asyncio.Task[None]] = {}

    def __init__(
        self,
        settings: Settings,
        redis_client: Redis,  # type: ignore[type-arg]
        notifier: TelegramNotifier | None = None,
    ) -> None:
        self._settings = settings
        self._redis = redis_client
        self._accounts = CursorAccountService(settings, redis_client)
        self._notifier = notifier or TelegramNotifier(settings)

    def is_cli_available(self) -> bool:
        cli = self._settings.cursor_agent_bin
        if Path(cli).is_file():
            return True
        return shutil.which(cli) is not None

    @staticmethod
    def validate_account_id(account_id: str) -> None:
        if not ACCOUNT_ID_PATTERN.fullmatch(account_id):
            raise CursorAccountLoginError(
                "Id аккаунта: латиница, цифры, дефис и нижнее подчеркивание, "
                "от 1 до 32 символов."
            )

    async def queue_login_start(
        self,
        db: AsyncSession,
        task_queue: TaskQueue,
        user_id: uuid.UUID,
        telegram_id: int,
        account_id: str,
        *,
        menu_message: dict[str, int | str] | None = None,
    ) -> str:
        self.validate_account_id(account_id)
        if await self._has_pending(telegram_id):
            raise CursorAccountLoginError(
                "Уже жду вход. Открой ссылку в этом меню "
                "или нажми «Отменить вход»."
            )
        tasks = TaskRepository(db)
        payload: dict[str, object] = {
            "account_id": account_id,
            "telegram_id": telegram_id,
        }
        if menu_message is not None:
            payload["menu_message"] = menu_message
        task = await tasks.create(
            user_id=user_id,
            task_type="cursor_account_login",
            payload=json.dumps(payload, ensure_ascii=False),
        )
        await db.commit()
        await task_queue.enqueue(str(task.id))
        return "Запускаю авторизацию Cursor на сервере…"

    async def queue_login_cancel(
        self,
        db: AsyncSession,
        task_queue: TaskQueue,
        user_id: uuid.UUID,
        telegram_id: int,
    ) -> str:
        if not await self._has_pending(telegram_id):
            return "Нет активной авторизации Cursor."
        tasks = TaskRepository(db)
        task = await tasks.create(
            user_id=user_id,
            task_type="cursor_account_login_cancel",
            payload=json.dumps({"telegram_id": telegram_id}, ensure_ascii=False),
        )
        await db.commit()
        await task_queue.enqueue(str(task.id))
        return "Отменяю авторизацию на сервере…"

    async def queue_logout(
        self,
        db: AsyncSession,
        task_queue: TaskQueue,
        user_id: uuid.UUID,
        telegram_id: int,
        *,
        menu_message: dict[str, int | str] | None = None,
    ) -> str:
        tasks = TaskRepository(db)
        payload: dict[str, object] = {"telegram_id": telegram_id}
        if menu_message is not None:
            payload["menu_message"] = menu_message
        task = await tasks.create(
            user_id=user_id,
            task_type="cursor_account_logout",
            payload=json.dumps(payload, ensure_ascii=False),
        )
        await db.commit()
        await task_queue.enqueue(str(task.id))
        return "Выхожу из Cursor…"

    async def queue_account_switch(
        self,
        db: AsyncSession,
        task_queue: TaskQueue,
        user_id: uuid.UUID,
        telegram_id: int,
        account_id: str,
        *,
        menu_message: dict[str, int | str] | None = None,
    ) -> str:
        self._accounts.get_account(account_id)
        payload: dict[str, object] = {
            "account_id": account_id,
            "telegram_id": telegram_id,
        }
        if menu_message is not None:
            payload["menu_message"] = menu_message
        tasks = TaskRepository(db)
        task = await tasks.create(
            user_id=user_id,
            task_type="cursor_account_switch",
            payload=json.dumps(payload, ensure_ascii=False),
        )
        await db.commit()
        await task_queue.enqueue(str(task.id))
        return f"Переключаю активный аккаунт на `{account_id}` на сервере…"

    async def start_login(
        self,
        telegram_id: int,
        account_id: str,
        *,
        menu_message: dict[str, int | str] | None = None,
    ) -> LoginStartResult:
        self.validate_account_id(account_id)
        if await self._has_pending(telegram_id):
            raise CursorAccountLoginError(
                "Уже жду вход. Открой ссылку в этом меню "
                "или нажми «Отменить вход»."
            )

        await self._cancel_monitor(telegram_id)
        home = self._accounts.prepare_account_directory(account_id)
        auth_file = home / ".config" / "cursor" / "auth.json"
        env = os.environ.copy()
        env["HOME"] = str(home)
        env["NO_OPEN_BROWSER"] = "1"

        process = await asyncio.create_subprocess_exec(
            self._settings.cursor_agent_bin,
            "login",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=env,
        )
        if process.stdout is None:
            raise CursorAccountLoginError("Не удалось запустить `agent login`.")

        try:
            login_url = await self._read_login_url(process)
        except CursorAccountLoginError:
            await self._terminate_process(process)
            raise

        await self._set_pending(telegram_id, account_id, process.pid or 0, str(home))
        monitor = asyncio.create_task(
            self._monitor_login(
                telegram_id=telegram_id,
                account_id=account_id,
                process=process,
                auth_file=auth_file,
                menu_message=menu_message,
            )
        )
        self._monitor_tasks[telegram_id] = monitor
        return LoginStartResult(account_id=account_id, login_url=login_url)

    async def start_login_and_notify(
        self,
        telegram_id: int,
        account_id: str,
        *,
        menu_message: dict[str, int | str] | None = None,
    ) -> str:
        result = await self.start_login(
            telegram_id, account_id, menu_message=menu_message
        )
        await self.publish_login_start(telegram_id, result, menu_message)
        return CURSOR_LOGIN_TASK_NOTIFIED

    async def publish_login_start(
        self,
        telegram_id: int,
        result: LoginStartResult,
        menu_message: dict[str, int | str] | None,
    ) -> None:
        """Put the login link into the menu message instead of a new message."""
        if menu_message is None:
            await self.notify_login_start(telegram_id, result)
            return
        try:
            chat_id = int(menu_message["chat_id"])
            message_id = int(menu_message["message_id"])
        except (KeyError, TypeError, ValueError):
            await self.notify_login_start(telegram_id, result)
            return
        cancel_btn = InlineKeyboardButton(
            text="✖️ Отменить вход", callback_data="menu:act:account_cancel"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔑 Войти в Cursor", url=result.login_url
                    )
                ],
                [cancel_btn],
                [
                    InlineKeyboardButton(
                        text="← Назад", callback_data="menu:sub:settings"
                    )
                ],
            ]
        )
        try:
            await self._notifier.edit_live_message(
                chat_id,
                message_id,
                self.format_start_message(result),
                reply_markup=keyboard,
                rich_markdown=True,
            )
        except Exception:
            logger.exception("login_start_menu_edit_failed")
            await self.notify_login_start(telegram_id, result)

    async def notify_login_start(
        self, telegram_id: int, result: LoginStartResult
    ) -> None:
        await self._notifier.send_with_url_button(
            telegram_id,
            self.format_start_message(result),
            button_text="🔑 Войти в Cursor",
            url=result.login_url,
        )

    async def cancel_login(self, telegram_id: int) -> str:
        raw = await self._redis.get(self._session_key(telegram_id))
        if raw is None:
            return "Нет активной авторизации Cursor."

        payload = json.loads(raw)
        pid = int(payload.get("pid") or 0)
        if pid:
            await self._kill_pid(pid)

        await self._clear_pending(telegram_id)
        await self._cancel_monitor(telegram_id)
        return "Вход в Cursor отменён."

    async def has_pending_login(self, telegram_id: int) -> bool:
        return await self._has_pending(telegram_id)

    def format_start_message(self, result: LoginStartResult) -> str:
        return (
            "*Вход в Cursor*\n\n"
            "1. Нажми кнопку ниже и подтверди вход в браузере.\n"
            "2. Обычно это занимает до минуты."
        )

    async def _monitor_login(
        self,
        telegram_id: int,
        account_id: str,
        process: asyncio.subprocess.Process,
        auth_file: Path,
        menu_message: dict[str, int | str] | None = None,
    ) -> None:
        timeout = float(self._settings.cursor_account_login_timeout_seconds)
        try:
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except TimeoutError:
                await self._terminate_process(process)
                await self._publish_login_end(
                    telegram_id,
                    menu_message,
                    "Время ожидания входа истекло. Начни вход заново.",
                    logged_in=False,
                )
                return

            auth_file_exists = await asyncio.to_thread(auth_file.is_file)
            if process.returncode != 0 or not auth_file_exists:
                await self._publish_login_end(
                    telegram_id,
                    menu_message,
                    "Не удалось завершить вход. Попробуй ещё раз.",
                    logged_in=False,
                )
                return

            account = self._accounts.register_account(account_id, auth_file)
            try:
                snapshot = await self._accounts.fetch_usage(account)
                plan = snapshot.plan_name
            except CursorUsageError:
                plan = "unknown"

            await self._accounts.set_active_account(account.id)
            await self._refresh_models_note()
            await self._publish_login_end(
                telegram_id,
                menu_message,
                "Вход в Cursor выполнен.\n"
                f"План: *{plan}*",
                logged_in=True,
            )
        finally:
            await self._clear_pending(telegram_id)
            self._monitor_tasks.pop(telegram_id, None)

    async def _publish_login_end(
        self,
        telegram_id: int,
        menu_message: dict[str, int | str] | None,
        text: str,
        *,
        logged_in: bool,
    ) -> None:
        """Report login outcome in the menu message instead of a new one."""
        if menu_message is None:
            await self._notifier.send(telegram_id, text)
            return
        try:
            chat_id = int(menu_message["chat_id"])
            message_id = int(menu_message["message_id"])
        except (KeyError, TypeError, ValueError):
            await self._notifier.send(telegram_id, text)
            return
        try:
            await self._notifier.edit_live_message(
                chat_id,
                message_id,
                text,
                reply_markup=self._settings_menu_keyboard(telegram_id, logged_in),
                rich_markdown=True,
            )
        except Exception:
            logger.exception("login_end_menu_edit_failed")
            await self._notifier.send(telegram_id, text)

    def _settings_menu_keyboard(
        self, telegram_id: int, logged_in: bool
    ) -> InlineKeyboardMarkup:
        """Settings keyboard reflecting the state after the login attempt."""
        from telegram_cursor_agent.telegram.main_menu import (
            is_menu_owner,
            settings_submenu_keyboard,
        )

        return settings_submenu_keyboard(
            logged_in=logged_in,
            login_pending=False,
            owner=is_menu_owner(telegram_id, self._settings),
        )

    async def _refresh_models_note(self) -> str:
        """Refresh the catalog silently: its state is not part of login UX."""
        from telegram_cursor_agent.services.cursor_models import (
            CursorModelsError,
            refresh_models_catalog,
        )

        try:
            await asyncio.to_thread(refresh_models_catalog, self._settings)
        except CursorModelsError:
            logger.warning("login_models_catalog_refresh_failed")
        return ""

    async def _read_login_url(
        self, process: asyncio.subprocess.Process
    ) -> str:
        assert process.stdout is not None
        buffer = ""
        try:
            while True:
                chunk = await asyncio.wait_for(
                    process.stdout.read(4096),
                    timeout=LOGIN_READ_TIMEOUT_SECONDS,
                )
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                match = LOGIN_URL_PATTERN.search(buffer)
                if match:
                    return match.group(0)
        except TimeoutError:
            pass
        raise CursorAccountLoginError("Не удалось получить ссылку для входа в Cursor.")

    async def _has_pending(self, telegram_id: int) -> bool:
        return bool(await self._redis.get(self._session_key(telegram_id)))

    async def _set_pending(
        self, telegram_id: int, account_id: str, pid: int, home: str
    ) -> None:
        payload = json.dumps(
            {"account_id": account_id, "pid": pid, "home": home},
            ensure_ascii=False,
        )
        await self._redis.set(
            self._session_key(telegram_id),
            payload,
            ex=self._settings.cursor_account_login_timeout_seconds,
        )

    async def _clear_pending(self, telegram_id: int) -> None:
        await self._redis.delete(self._session_key(telegram_id))

    async def _cancel_monitor(self, telegram_id: int) -> None:
        task = self._monitor_tasks.pop(telegram_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    @staticmethod
    def _session_key(telegram_id: int) -> str:
        return f"{LOGIN_SESSION_KEY_PREFIX}{telegram_id}"

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except TimeoutError:
            process.kill()
            await process.wait()

    @staticmethod
    async def _kill_pid(pid: int) -> None:
        try:
            os.kill(pid, 15)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.5)
        try:
            os.kill(pid, 9)
        except ProcessLookupError:
            return
