"""Tool call formatting tests."""

from telegram_cursor_agent.agent.tool_call_format import format_tool_call_event


def test_format_shell_tool_call() -> None:
    event = {
        "type": "tool_call",
        "subtype": "started",
        "tool_call": {
            "shellToolCall": {
                "args": {"command": "ls -la /var/www"},
            }
        },
    }
    assert format_tool_call_event(event) == "🔧 Shell: ls -la /var/www"


def test_format_grep_tool_call() -> None:
    event = {
        "type": "tool_call",
        "subtype": "started",
        "tool_call": {
            "grepToolCall": {
                "args": {"pattern": "typing", "path": "/root/telegram-cursor-agent"},
            }
        },
    }
    text = format_tool_call_event(event)
    assert text is not None
    assert "Grep" in text
    assert "typing" in text


def test_skips_completed_tool_call() -> None:
    event = {
        "type": "tool_call",
        "subtype": "completed",
        "tool_call": {"shellToolCall": {"args": {"command": "echo"}}},
    }
    assert format_tool_call_event(event) is None
