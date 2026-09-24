"""Live preview composition with tool-call quotes."""

from unittest.mock import AsyncMock

from telegram_cursor_agent.agent.adapter import CursorAgentAdapter
from telegram_cursor_agent.agent.stream_progress import ToolAwareStreamProgressHandler
from telegram_cursor_agent.telegram.live_message import THINKING_STATUS_TEXT
from telegram_cursor_agent.telegram.live_tool_calls import (
    ToolCallLiveComposer,
    compose_live_with_tool_details,
    compose_live_with_tool_quotes,
)


def test_composer_accumulates_tools_under_thinking() -> None:
    composer = ToolCallLiveComposer(THINKING_STATUS_TEXT)
    composer.on_tool_call("🔧 Shell: ls")
    display = composer.display()
    assert THINKING_STATUS_TEXT in display
    assert display.lstrip().startswith("_")
    assert "\n\n>" in display
    assert "Shell: ls" in display


def test_composer_resets_tools_on_new_step() -> None:
    composer = ToolCallLiveComposer(THINKING_STATUS_TEXT)
    composer.on_tool_call("🔧 Shell: ls")
    display = composer.on_planning_step("Проверю код.")
    assert "Проверю код" in display
    assert "Shell" not in display
    assert "Shell" not in display


def test_rich_details_block_instead_of_blockquote() -> None:
    text = compose_live_with_tool_details("💬 Шаг", ["🔧 Shell: ls"])
    assert "<blockquote>" in text
    assert "expandable" not in text
    assert "Инструменты" not in text
    assert "Shell: ls" in text
    assert "<i>" in text
    assert "<code>" not in text


def test_rich_tool_list_chronological_newest_at_bottom() -> None:
    text = compose_live_with_tool_details(
        "💬 Шаг",
        ["🔧 Shell: first", "🔧 Read: second", "🔧 Grep: third"],
    )
    assert text.index("first") < text.index("second") < text.index("third")


def test_rich_composer_uses_details_when_enabled() -> None:
    composer = ToolCallLiveComposer(THINKING_STATUS_TEXT, use_rich_details=True)
    composer.on_tool_call("🔧 Shell: ls")
    display = composer.display()
    assert "<blockquote>" in display
    assert "expandable" not in display
    assert "\n\n>" not in display


def test_composer_keeps_at_most_eight_tool_calls() -> None:
    composer = ToolCallLiveComposer(THINKING_STATUS_TEXT)
    for index in range(9):
        composer.on_tool_call(f"🔧 Shell: cmd-{index}")
    assert len(composer._tools) == 8
    assert composer._tools[0] == "🔧 Shell: cmd-1"
    assert composer._tools[-1] == "🔧 Shell: cmd-8"
    display = composer.display()
    assert "cmd-0" not in display
    assert "cmd\\-8" in display


def test_composer_skips_consecutive_duplicate_tool_call() -> None:
    composer = ToolCallLiveComposer(THINKING_STATUS_TEXT)
    composer.on_tool_call("🔧 Shell: ls")
    composer.on_tool_call("🔧 Shell: ls")
    assert len(composer._tools) == 1
    composer.on_tool_call("🔧 Read: a")
    composer.on_tool_call("🔧 Read: a")
    assert composer._tools == ["🔧 Shell: ls", "🔧 Read: a"]


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
