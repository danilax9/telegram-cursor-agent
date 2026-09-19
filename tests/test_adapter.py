"""Cursor agent adapter tests."""

from telegram_cursor_agent.agent.adapter import CursorAgentAdapter


def test_build_command_basic(test_settings, runner) -> None:
    adapter = CursorAgentAdapter(test_settings, runner)
    cmd = adapter.build_command("/workspace/proj", "fix the bug")
    assert cmd == [
        "cursor-agent",
        "--print",
        "--output-format",
        "stream-json",
        "--stream-partial-output",
        "--workspace",
        "/workspace/proj",
        "--trust",
        "--force",
        "--sandbox",
        "disabled",
        "fix the bug",
    ]


def test_build_command_with_resume(test_settings, runner) -> None:
    adapter = CursorAgentAdapter(test_settings, runner)
    cmd = adapter.build_command("/workspace/proj", "continue", resume_chat_id="chat-abc")
    assert "--resume" in cmd
    assert "chat-abc" in cmd
    assert cmd[-1] == "continue"


def test_parse_stream_output_uses_last_assistant(test_settings, runner) -> None:
    adapter = CursorAgentAdapter(test_settings, runner)
    user_event = (
        '{"type":"user","message":{"role":"user",'
        '"content":[{"type":"text","text":"secret prompt"}]}}\n'
    )
    step_event = (
        '{"type":"assistant","model_call_id":"c1","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"step one"}]}}\n'
    )
    final_event = (
        '{"type":"assistant","message":{"role":"assistant",'
        '"content":[{"type":"text","text":"Final answer"}]}}\n'
    )
    result_event = (
        '{"type":"result","subtype":"success","result":"step oneFinal answer",'
        '"session_id":"sess-123"}\n'
    )
    stdout = user_event + step_event + final_event + result_event
    events, output, chat_id = adapter._parse_stream_output(stdout)
    assert len(events) == 4
    assert output == "Final answer"
    assert chat_id == "sess-123"


def test_parse_stream_output_legacy(test_settings, runner) -> None:
    adapter = CursorAgentAdapter(test_settings, runner)
    stdout = (
        '{"type":"text","text":"Hello"}\n'
        '{"type":"done","chat_id":"sess-123"}\n'
    )
    events, output, chat_id = adapter._parse_stream_output(stdout)
    assert len(events) == 2
    assert "Hello" in output
    assert chat_id == "sess-123"


def test_parse_plain_text_fallback(test_settings, runner) -> None:
    adapter = CursorAgentAdapter(test_settings, runner)
    events, output, chat_id = adapter._parse_stream_output("plain output\nline 2")
    assert len(events) == 0
    assert "plain output" in output
    assert chat_id is None
