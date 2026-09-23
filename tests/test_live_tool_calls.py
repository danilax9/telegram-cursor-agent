"""Live preview composition with tool-call quotes."""

from unittest.mock import AsyncMock

from telegram_cursor_agent.agent.adapter import CursorAgentAdapter
from telegram_cursor_agent.agent.stream_progress import ToolAwareStreamProgressHandler
from telegram_cursor_agent.telegram.live_message import THINKING_STATUS_TEXT
from telegram_cursor_agent.telegram.live_tool_calls import ToolCallLiveComposer


def test_composer_accumulates_tools_under_thinking() -> None:
    composer = ToolCallLiveComposer(THINKING_STATUS_TEXT)
    composer.on_tool_call("🔧 Shell: ls")
    display = composer.display()
    assert display.startswith(THINKING_STATUS_TEXT)
    assert "\n\n>" in display
    assert "Shell: ls" in display


def test_composer_resets_tools_on_new_step() -> None:
    composer = ToolCallLiveComposer(THINKING_STATUS_TEXT)
    composer.on_tool_call("🔧 Shell: ls")
    display = composer.on_planning_step("Проверю код.")
    assert "Проверю код" in display
    assert "Shell" not in display
    assert "Shell" not in display


def test_composer_keeps_at_most_five_tool_calls() -> None:
    composer = ToolCallLiveComposer(THINKING_STATUS_TEXT)
    for index in range(6):
        composer.on_tool_call(f"🔧 Shell: cmd-{index}")
    assert len(composer._tools) == 5
    assert composer._tools[0] == "🔧 Shell: cmd-1"
    assert composer._tools[-1] == "🔧 Shell: cmd-5"
    display = composer.display()
    assert "cmd-0" not in display
    assert "cmd\\-5" in display


def test_composer_drops_oldest_tools_when_single_line_exceeds_telegram_limit() -> None:
    composer = ToolCallLiveComposer(THINKING_STATUS_TEXT)
    long_line = "🔧 Shell: " + ("x" * 5000)
    display = composer.on_tool_call(long_line)
    assert THINKING_STATUS_TEXT in display
    assert "Shell" not in display
    assert not composer._tools


async def test_tool_aware_handler_emits_quotes_before_first_step() -> None:
    on_progress = AsyncMock()
    handler = ToolAwareStreamProgressHandler(on_progress, CursorAgentAdapter._event_text)
    await handler.handle(
        {
            "type": "tool_call",
            "subtype": "started",
            "tool_call": {
                "shellToolCall": {"args": {"command": "pwd"}},
            },
        }
    )
    on_progress.assert_awaited_once()
    text = on_progress.await_args.args[0]
    assert THINKING_STATUS_TEXT in text
    assert "\n\n>" in text
    assert "Shell: pwd" in text
