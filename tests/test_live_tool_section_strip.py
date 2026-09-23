from telegram_cursor_agent.core.security import strip_live_tool_section
from telegram_cursor_agent.telegram.live_tool_calls import compose_live_with_tool_details


def test_strip_rich_details_section() -> None:
    text = compose_live_with_tool_details("💬 Base", ["🔧 Shell: ls"])
    assert strip_live_tool_section(text) == "💬 Base"


def test_strip_blockquote_section() -> None:
    from telegram_cursor_agent.telegram.live_tool_calls import compose_live_with_tool_quotes

    text = compose_live_with_tool_quotes("💬 Base", ["🔧 Shell: ls"])
    assert strip_live_tool_section(text) == "💬 Base"
