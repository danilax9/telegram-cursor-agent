"""Cursor agent adapter tests."""

from pathlib import Path

from telegram_cursor_agent.agent.adapter import CursorAgentAdapter


def test_build_command_basic(test_settings, runner) -> None:
    adapter = CursorAgentAdapter(test_settings, runner)
    cmd = adapter.build_command("/workspace/proj", "fix the bug")
    assert cmd[:-1] == [
        test_settings.cursor_agent_bin,
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
        "--approve-mcps",
    ]
    assert cmd[-1].endswith("fix the bug")
    assert "telegram-cursor-agent" in cmd[-1]
    assert "~/.hermes/skills" in cmd[-1]


def test_build_command_uses_model_from_projects_root(
    test_settings, runner, tmp_workspace
) -> None:
    (tmp_workspace / ".cursor_model").write_text("gpt-5\n")
    settings = test_settings.model_copy(update={"projects_root": tmp_workspace})
    adapter = CursorAgentAdapter(settings, runner)
    cmd = adapter.build_command("/", "hello")
    assert "--model" in cmd
    assert "gpt-5" in cmd


def test_build_command_syncs_rules_to_workspace_and_projects_root(
    test_settings, runner, tmp_path: Path
) -> None:
    projects_root = tmp_path / "projects"
    projects_root.mkdir()
    workspace = tmp_path / "opt" / "my-app"
    workspace.mkdir(parents=True)
    settings = test_settings.model_copy(update={"projects_root": projects_root})
    adapter = CursorAgentAdapter(settings, runner)
    adapter.build_command(str(workspace), "hello")

    for root in (projects_root, workspace):
        rules_dir = root / ".cursor" / "rules"
        assert (rules_dir / "telegram-bot.mdc").is_file()
        assert (rules_dir / "skills-routing.mdc").is_file()
        assert (rules_dir / "modern-web.mdc").is_file()
        routing = (rules_dir / "skills-routing.mdc").read_text(encoding="utf-8")
        assert "Mandatory routing" in routing


def test_build_command_with_resume(test_settings, runner) -> None:
    adapter = CursorAgentAdapter(test_settings, runner)
    cmd = adapter.build_command("/workspace/proj", "continue", resume_chat_id="chat-abc")
    assert "--resume" in cmd
    assert "chat-abc" in cmd
    assert cmd[-1].endswith("continue")
    assert "telegram-cursor-agent" in cmd[-1]


def test_build_command_keeps_system_recovery_prompt(test_settings, runner) -> None:
    adapter = CursorAgentAdapter(test_settings, runner)
    raw = "[System: Self-deploy finished successfully.]"
    cmd = adapter.build_command("/workspace/proj", raw)
    assert cmd[-1] == raw


def test_rule_sync_includes_filesystem_root(test_settings, runner) -> None:
    adapter = CursorAgentAdapter(test_settings, runner)
    assert Path("/") in adapter._rule_sync_roots("/")


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
