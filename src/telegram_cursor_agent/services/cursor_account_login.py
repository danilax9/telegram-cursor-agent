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

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from telegram_cursor_agent.core.config import Settings
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
    ) -> str:
        self.validate_account_id(account_id)
        if await self._has_pending(telegram_id):
            raise CursorAccountLoginError(
                "Уже жду авторизацию. Открой ссылку из прошлого сообщения "
                "или отмени: `/account cancel`"
            )
        tasks = TaskRepository(db)
        task = await tasks.create(
            user_id=user_id,
            task_type="cursor_account_login",
            payload=json.dumps(
                {"account_id": account_id, "telegram_id": telegram_id},
                ensure_ascii=False,
            ),
        )
        await task_queue.enqueue(str(task.id))
        return f"Запускаю авторизацию для `{account_id}` на сервере…"

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
        await task_queue.enqueue(str(task.id))
        return "Отменяю авторизацию на сервере…"

    async def start_login(self, telegram_id: int, account_id: str) -> LoginStartResult:
        self.validate_account_id(account_id)
        if await self._has_pending(telegram_id):
            raise CursorAccountLoginError(
                "Уже жду авторизацию. Открой ссылку из прошлого сообщения "
                "или отмени: `/account cancel`"
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
            )
        )
        self._monitor_tasks[telegram_id] = monitor
        return LoginStartResult(account_id=account_id, login_url=login_url)

    async def start_login_and_notify(
        self, telegram_id: int, account_id: str
    ) -> str:
        result = await self.start_login(telegram_id, account_id)
        await self.notify_login_start(telegram_id, result)
        return CURSOR_LOGIN_TASK_NOTIFIED

    async def notify_login_start(
        self, telegram_id: int, result: LoginStartResult
    ) -> None:
        await self._notifier.send_with_url_button(
            telegram_id,
            self.format_start_message(result),
            button_text="Войти в Cursor",
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
        account_id = str(payload.get("account_id", ""))
        return f"Авторизация для `{account_id}` отменена."

    def format_start_message(self, result: LoginStartResult) -> str:
        return (
            f"*Добавление аккаунта Cursor:* `{result.account_id}`\n\n"
            "1. Нажми кнопку ниже и войди в Cursor в браузере.\n"
            "2. После входа бот сам сохранит аккаунт.\n"
            "3. Обычно это занимает до минуты.\n\n"
            f"Ссылка: {result.login_url}"
        )

    async def _monitor_login(
        self,
        telegram_id: int,
        account_id: str,
        process: asyncio.subprocess.Process,
        auth_file: Path,
    ) -> None:
        timeout = float(self._settings.cursor_account_login_timeout_seconds)
        try:
            try:
                await asyncio.wait_for(process.wait(), timeout=timeout)
            except TimeoutError:
                await self._terminate_process(process)
                await self._notifier.send(
                    telegram_id,
                    f"Время ожидания входа для `{account_id}` истекло. "
                    f"Повтори: `/account add {account_id}`",
                )
                return

            auth_file_exists = await asyncio.to_thread(auth_file.is_file)
            if process.returncode != 0 or not auth_file_exists:
                await self._notifier.send(
                    telegram_id,
                    f"Не удалось завершить вход для `{account_id}`. "
                    f"Код выхода: {process.returncode or 'unknown'}.",
                )
                return

            account = self._accounts.register_account(account_id, auth_file)
            try:
                snapshot = await self._accounts.fetch_usage(account)
                plan = snapshot.plan_name
            except CursorUsageError:
                plan = "unknown"

            await self._accounts.set_active_account(account.id)
            await self._notifier.send(
                telegram_id,
                f"Аккаунт `{account.id}` добавлен и активирован.\n"
                f"План: *{plan}*\n"
                "Проверить лимиты: `/limits`",
            )
        finally:
            await self._clear_pending(telegram_id)
            self._monitor_tasks.pop(telegram_id, None)

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
