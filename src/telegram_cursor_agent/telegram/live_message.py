"""Single Telegram message updated in place for live agent progress."""

import asyncio

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.logging import get_logger
from telegram_cursor_agent.core.security import (
    escape_telegram_markdown_v2,
    prepare_agent_reply_text,
    sanitize_for_telegram,
    sanitize_for_telegram_markdown_v2,
    sanitize_for_telegram_rich,
    split_telegram_message,
)
from telegram_cursor_agent.telegram.notifier import TelegramNotifier

logger = get_logger(__name__)

THINKING_STATUS_TEXT = "🧠 Думаю"
_PROGRESS_PREFIX = "💬 "


def format_progress_message(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    if stripped.startswith("💬") or stripped.startswith("💭"):
        return stripped.replace("💭 ", "💬 ", 1) if stripped.startswith("💭") else stripped
    return f"{_PROGRESS_PREFIX}{stripped}"


def escape_rich_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def wrap_live_progress_rich_html(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return f"<i>{escape_rich_html(stripped)}</i>"


def wrap_live_progress_markdown_v2(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return f"_{escape_telegram_markdown_v2(stripped)}_"


def wrap_live_progress_rich_markdown(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return f"*{stripped}*"


class LiveMessageNotifier:
    """Send once, then replace the same message on every progress update."""

    def __init__(
        self,
        notifier: TelegramNotifier,
        settings: Settings,
        telegram_id: int,
        *,
        tool_calls_in_live: bool = False,
    ) -> None:
        self._notifier = notifier
        self._settings = settings
        self._telegram_id = telegram_id
        self._tool_calls_in_live = tool_calls_in_live
        self._message_id: int | None = None
        self._last_text: str | None = None
        self._pending_display: str | None = None
        self._debounce_task: asyncio.Task[None] | None = None

    @property
    def message_id(self) -> int | None:
        return self._message_id

    async def update(self, text: str) -> None:
        try:
            safe_text = self._prepare_intermediate_text(format_progress_message(text))
            await self._apply_intermediate_text(safe_text)
        except Exception:
            logger.exception("live_progress_failed")

    async def replace_display(self, text: str) -> None:
        """Update live text as-is (already composed for display)."""
        try:
            prepared = self._prepare_display_text(text)
            if (
                self._tool_calls_in_live
                and self._settings.telegram_live_tool_debounce_seconds > 0
            ):
                self._pending_display = prepared
                if self._debounce_task is None or self._debounce_task.done():
                    self._debounce_task = asyncio.create_task(self._flush_debounced_display())
                return
            await self._push_display_text(prepared)
        except Exception:
            logger.exception("live_progress_failed")

    def _prepare_display_text(self, text: str) -> str:
        if self._tool_calls_in_live:
            if self._settings.telegram_uses_rich_messages:
                return sanitize_for_telegram_rich(
                    text.strip(), self._settings.cursor_agent_max_output_bytes
                )
            return sanitize_for_telegram_markdown_v2(
                text.strip(),
                self._settings.cursor_agent_max_output_bytes,
            )
        return sanitize_for_telegram(
            text.strip(),
            self._settings.cursor_agent_max_output_bytes,
        )

    async def _flush_debounced_display(self) -> None:
        try:
            await asyncio.sleep(self._settings.telegram_live_tool_debounce_seconds)
            pending = self._pending_display
            if pending is not None:
                await self._push_display_text(pending)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("live_progress_failed")

    async def flush_pending_display(self) -> None:
        """Apply the latest debounced tool-call preview immediately."""
        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()
            try:
                await self._debounce_task
            except asyncio.CancelledError:
                pass
            self._debounce_task = None
        pending = self._pending_display
        if pending is not None:
            await self._push_display_text(pending)
            self._pending_display = None

    async def _push_display_text(self, safe_text: str) -> None:
        if self._tool_calls_in_live:
            if self._settings.telegram_uses_rich_messages:
                await self._apply_live_text(safe_text)
                return
            await self._apply_live_text(safe_text, markdown_v2=True)
            return
        await self._apply_live_text(safe_text)

    async def update_status(self, text: str) -> None:
        """Task-level status (redirect, resume) without the thought prefix."""
        try:
            safe_text = self._prepare_intermediate_text(text)
            await self._apply_intermediate_text(safe_text)
        except Exception:
            logger.exception("live_status_failed")

    def _prepare_intermediate_text(self, text: str) -> str:
        stripped = text.strip()
        max_bytes = self._settings.cursor_agent_max_output_bytes
        if self._settings.telegram_uses_rich_messages:
            if self._tool_calls_in_live:
                wrapped = wrap_live_progress_rich_html(stripped)
            else:
                wrapped = wrap_live_progress_rich_markdown(stripped)
            return sanitize_for_telegram_rich(wrapped, max_bytes)
        wrapped = wrap_live_progress_markdown_v2(stripped)
        return sanitize_for_telegram_markdown_v2(wrapped, max_bytes)

    async def _apply_intermediate_text(self, safe_text: str) -> None:
        if self._settings.telegram_uses_rich_messages:
            if self._tool_calls_in_live:
                await self._apply_live_text(safe_text, rich_html=True)
                return
            await self._apply_live_text(safe_text, rich_markdown=True)
            return
        await self._apply_live_text(safe_text, markdown_v2=True)

    async def _apply_live_text(
        self,
        safe_text: str,
        *,
        markdown_v2: bool = False,
        rich_markdown: bool = False,
        rich_html: bool = False,
    ) -> None:
        if not safe_text or safe_text == self._last_text:
            return

        self._last_text = safe_text
        use_rich = rich_markdown or (
            self._tool_calls_in_live and self._settings.telegram_uses_rich_messages
        )
        use_rich_html = rich_html or (
            self._tool_calls_in_live and self._settings.telegram_uses_rich_messages
        )
        use_v2 = (
            markdown_v2
            or (self._tool_calls_in_live and not self._settings.telegram_uses_rich_messages)
        )
        if self._message_id is None:
            self._message_id = await self._notifier.send_live_start(
                self._telegram_id,
                safe_text,
                markdown_v2=use_v2,
                rich_markdown=use_rich and not use_rich_html,
                rich_html=use_rich_html,
            )
            return

        await self._notifier.edit_live_message(
            self._telegram_id,
            self._message_id,
            safe_text,
            markdown_v2=use_v2,
            rich_markdown=use_rich and not use_rich_html,
            rich_html=use_rich_html,
        )

    async def finalize(self, text: str) -> None:
        safe_text = prepare_agent_reply_text(text.strip(), self._settings)
        if not safe_text:
            return

        for chunk in split_telegram_message(safe_text):
            await self._notifier.send(self._telegram_id, chunk)
        await self._delete_progress_message()

    async def _delete_progress_message(self) -> None:
        if self._message_id is None:
            return
        message_id = self._message_id
        self._message_id = None
        try:
            await self._notifier.delete_message(self._telegram_id, message_id)
        except Exception:
            logger.warning(
                "live_progress_delete_failed",
                telegram_id=self._telegram_id,
                message_id=message_id,
            )
