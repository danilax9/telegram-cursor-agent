"""Compose live Telegram preview with quoted or rich collapsible tool steps."""

from __future__ import annotations

from telegram_cursor_agent.core.security import (
    TELEGRAM_MESSAGE_MAX_CHARS,
    escape_telegram_markdown_v2,
)
from telegram_cursor_agent.telegram.live_message import (
    THINKING_STATUS_TEXT,
    format_progress_message,
    wrap_live_progress_markdown_v2,
    wrap_live_progress_rich_html,
)

LIVE_TOOL_CALLS_MAX = 8


def _blockquote_line(line: str) -> str:
    return f">{escape_telegram_markdown_v2(line)}"


def compose_live_with_tool_quotes(base_text: str, tool_lines: list[str]) -> str:
    """MarkdownV2 live card: base line + blockquoted tool steps."""
    base = wrap_live_progress_markdown_v2(base_text)
    if not tool_lines:
        return base
    quotes = "\n".join(_blockquote_line(line) for line in tool_lines)
    return f"{base}\n\n{quotes}"


def _escape_rich_html(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def compose_live_with_tool_details(
    base_text: str,
    tool_lines: list[str],
    *,
    expandable: bool = False,
) -> str:
    """Rich Message HTML: planning line + blockquote (chronological, newest at bottom)."""
    base = wrap_live_progress_rich_html(base_text)
    if not tool_lines:
        return base
    tool_rows = "<br>".join(
        _escape_rich_html(line.strip()) for line in tool_lines
    )
    tag = "blockquote expandable" if expandable else "blockquote"
    return f"{base}\n\n<{tag}>{tool_rows}</blockquote>"


class ToolCallLiveComposer:
    """One live message: base planning text + tool calls until the next step."""

    def __init__(
        self,
        initial_base: str = THINKING_STATUS_TEXT,
        *,
        use_rich_details: bool = False,
        tool_expandable: bool = False,
    ) -> None:
        self._base = initial_base.strip()
        self._tools: list[str] = []
        self._use_rich_details = use_rich_details
        self._tool_expandable = tool_expandable

    def on_planning_step(self, step_text: str) -> str:
        self._base = format_progress_message(step_text)
        self._tools = []
        return self.display()

    def on_tool_call(self, summary: str) -> str:
        line = summary.strip()
        if line:
            if self._tools and self._tools[-1] == line:
                return self.display()
            self._tools.append(line)
            while len(self._tools) > LIVE_TOOL_CALLS_MAX:
                self._tools.pop(0)
        return self.display()

    def _compose(self, base: str, tools: list[str]) -> str:
        if self._use_rich_details:
            return compose_live_with_tool_details(
                base, tools, expandable=self._tool_expandable
            )
        return compose_live_with_tool_quotes(base, tools)

    def display(self) -> str:
        while self._tools:
            text = self._compose(self._base, self._tools)
            if len(text) <= TELEGRAM_MESSAGE_MAX_CHARS:
                return text
            self._tools.pop(0)
        return self._compose(self._base, self._tools)
