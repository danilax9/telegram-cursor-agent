"""Track Cursor stream-json events and emit live planning steps."""

from collections.abc import Awaitable, Callable
from typing import Any


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
