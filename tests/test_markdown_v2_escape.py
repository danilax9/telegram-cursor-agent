from telegram_cursor_agent.core.security import escape_telegram_markdown_v2
from telegram_cursor_agent.telegram.live_tool_calls import compose_live_with_tool_quotes


def test_escape_dots_in_paths() -> None:
    assert "\\." in escape_telegram_markdown_v2("file.py")


def test_blockquote_prefix_per_tool_line() -> None:
    text = compose_live_with_tool_quotes("🧠 Думаю", ["🔧 Shell: ls -la"])
    assert text.startswith("🧠 Думаю")
    assert "\n\n>🔧 Shell: ls \\-la" in text or "\n\n>🔧 Shell: ls -la" in text
