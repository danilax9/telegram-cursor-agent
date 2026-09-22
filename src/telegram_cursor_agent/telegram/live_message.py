"""Single Telegram message updated in place for live agent progress."""

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import sanitize_for_telegram, split_telegram_message
from telegram_cursor_agent.telegram.notifier import TelegramNotifier

THINKING_STATUS_TEXT = "🧠 Думаю"
_PROGRESS_PREFIX = "💬 "


def format_progress_message(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.startswith("💬") or stripped.startswith("💭"):
        return stripped.replace("💭 ", "💬 ", 1) if stripped.startswith("💭") else stripped
    return f"{_PROGRESS_PREFIX}{stripped}"


class LiveMessageNotifier:
    """Send once, then replace the same message on every progress update."""

    def __init__(
        self, notifier: TelegramNotifier, settings: Settings, telegram_id: int
    ) -> None:
        self._notifier = notifier
        self._settings = settings
        self._telegram_id = telegram_id
        self._message_id: int | None = None
        self._last_text: str | None = None

    @property
    def message_id(self) -> int | None:
        return self._message_id

    async def update(self, text: str) -> None:
        safe_text = sanitize_for_telegram(
            format_progress_message(text),
            self._settings.cursor_agent_max_output_bytes,
        )
        await self._apply_live_text(safe_text)

    async def update_status(self, text: str) -> None:
        """Task-level status (redirect, resume) without the thought prefix."""
        safe_text = sanitize_for_telegram(
            text.strip(),
            self._settings.cursor_agent_max_output_bytes,
        )
        await self._apply_live_text(safe_text)

    async def _apply_live_text(self, safe_text: str) -> None:
        if not safe_text or safe_text == self._last_text:
            return

        self._last_text = safe_text
        if self._message_id is None:
            self._message_id = await self._notifier.send_live_start(
                self._telegram_id, safe_text
            )
            return

        await self._notifier.edit_live_message(
            self._telegram_id, self._message_id, safe_text
        )

    async def finalize(self, text: str) -> None:
        safe_text = sanitize_for_telegram(
            text.strip(), self._settings.cursor_agent_max_output_bytes
        )
        if not safe_text:
            return

        chunks = split_telegram_message(safe_text)
        if self._message_id is None:
            for chunk in chunks:
                await self._notifier.send(self._telegram_id, chunk)
            return

        await self._notifier.edit_live_message(
            self._telegram_id, self._message_id, chunks[0]
        )
        for chunk in chunks[1:]:
            await self._notifier.send(self._telegram_id, chunk)
