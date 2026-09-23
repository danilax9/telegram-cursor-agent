"""Track Cursor stream-json events and emit live planning steps."""

from collections.abc import Awaitable, Callable
from typing import Any

from telegram_cursor_agent.agent.tool_call_format import format_tool_call_event
from telegram_cursor_agent.telegram.live_message import THINKING_STATUS_TEXT
from telegram_cursor_agent.telegram.live_tool_calls import ToolCallLiveComposer


class StreamProgressHandler:
    """Emit one Telegram update per completed agent planning step."""

    def __init__(
        self,
        on_progress: Callable[[str], Awaitable[None]],
        event_text: Callable[[dict[str, Any], str], str],
    ) -> None:
        self._on_progress = on_progress
        self._event_text = event_text
        self.last_assistant = ""

    async def handle(self, data: dict[str, Any]) -> None:
        event_type = str(data.get("type", data.get("event", "")))
        if event_type == "assistant":
            await self._handle_assistant(data)

    async def _handle_assistant(self, data: dict[str, Any]) -> None:
        text = self._event_text(data, "assistant").strip()
        if not text:
            return
        self.last_assistant = text

        # Planning steps before tool calls arrive with model_call_id when
        # --stream-partial-output is enabled.
        if data.get("model_call_id"):
            await self._on_progress(text)


class ToolAwareStreamProgressHandler(StreamProgressHandler):
    """Planning steps reset the live card; tool calls accumulate as quotes underneath."""

    def __init__(
        self,
        on_progress: Callable[[str], Awaitable[None]],
        event_text: Callable[[dict[str, Any], str], str],
        *,
        initial_status: str = THINKING_STATUS_TEXT,
        use_rich_tool_details: bool = False,
    ) -> None:
        super().__init__(on_progress, event_text)
        self._composer = ToolCallLiveComposer(
            initial_status, use_rich_details=use_rich_tool_details
        )

    async def handle(self, data: dict[str, Any]) -> None:
        event_type = str(data.get("type", data.get("event", "")))
        if event_type == "tool_call":
            summary = format_tool_call_event(data)
            if summary is None:
                return
            await self._on_progress(self._composer.on_tool_call(summary))
            return
        if event_type == "assistant":
            await self._handle_assistant_step(data)

    async def _handle_assistant_step(self, data: dict[str, Any]) -> None:
        text = self._event_text(data, "assistant").strip()
        if not text:
            return
        self.last_assistant = text
        if not data.get("model_call_id"):
            return
        await self._on_progress(self._composer.on_planning_step(text))
