"""Compose live Telegram preview with quoted or rich collapsible tool steps."""

from __future__ import annotations

from telegram_cursor_agent.core.security import (
    TELEGRAM_MESSAGE_MAX_CHARS,
    escape_telegram_markdown_v2,
)
from telegram_cursor_agent.telegram.live_message import (
    THINKING_STATUS_TEXT,
    format_progress_message,
)

LIVE_TOOL_CALLS_MAX = 5
_RICH_DETAILS_MARKER = "# Details"


def _blockquote_line(line: str) -> str:
    return f">{escape_telegram_markdown_v2(line)}"


def compose_live_with_tool_quotes(base_text: str, tool_lines: list[str]) -> str:
    """MarkdownV2 live card: base line + blockquoted tool steps."""
    base = escape_telegram_markdown_v2(base_text.strip())
    if not tool_lines:
        return base
    quotes = "\n".join(_blockquote_line(line) for line in tool_lines)
    return f"{base}\n\n{quotes}"


def _rich_tool_list_item(line: str) -> str:
    safe = line.strip().replace("`", "'")
    return f"- `{safe}`"


def compose_live_with_tool_details(base_text: str, tool_lines: list[str]) -> str:
    """Rich Message live card: planning line + collapsible Details with tool list."""
    base = base_text.strip()
    if not tool_lines:
        return base
    count = len(tool_lines)
    summary = f"🔧 Инструменты ({count})"
    items = "\n".join(_rich_tool_list_item(line) for line in tool_lines)
    return (
        f"{base}\n\n"
        f"{_RICH_DETAILS_MARKER}\n\n"
        f" {summary}\n\n"
        f"### Вызовы\n"
        f"{items}"
    )


class ToolCallLiveComposer:
    """One live message: base planning text + tool calls until the next step."""

    def __init__(
        self,
        initial_base: str = THINKING_STATUS_TEXT,
        *,
        use_rich_details: bool = False,
    ) -> None:
        self._base = initial_base.strip()
        self._tools: list[str] = []
        self._use_rich_details = use_rich_details

    def on_planning_step(self, step_text: str) -> str:
        self._base = format_progress_message(step_text)
        self._tools = []
        return self.display()

    def on_tool_call(self, summary: str) -> str:
        line = summary.strip()
        if line:
            self._tools.append(line)
            while len(self._tools) > LIVE_TOOL_CALLS_MAX:
                self._tools.pop(0)
        return self.display()

    def _compose(self, base: str, tools: list[str]) -> str:
        if self._use_rich_details:
            return compose_live_with_tool_details(base, tools)
        return compose_live_with_tool_quotes(base, tools)

    def display(self) -> str:
        while self._tools:
            text = self._compose(self._base, self._tools)
            if len(text) <= TELEGRAM_MESSAGE_MAX_CHARS:
                return text
            self._tools.pop(0)
        return self._compose(self._base, self._tools)
