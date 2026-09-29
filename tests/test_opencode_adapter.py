"""OpenCode headless adapter."""

import json

from telegram_cursor_agent.agent.opencode_adapter import (
    OpenCodeAgentAdapter,
    _parse_stream_output,
)
from telegram_cursor_agent.services.cursor_model_catalog import detect_family
from telegram_cursor_agent.services.opencode_models import (
    OPENCODE_FREE_MODELS,
    is_opencode_model,
)


def test_build_command_uses_verified_flags(test_settings, runner, tmp_workspace) -> None:
    (tmp_workspace / ".cursor_model").write_text(
        "opencode/mimo-v2.6-flash-free\n", encoding="utf-8"
    )
    settings = test_settings.model_copy(
        update={
            "projects_root": tmp_workspace,
            "opencode_cli_path": "/usr/local/bin/opencode",
        }
    )
    adapter = OpenCodeAgentAdapter(settings, runner)
    cmd = adapter.build_command("/workspace/proj", "fix the bug", resume_chat_id="chat-abc")
    assert cmd[:7] == [
        "/usr/local/bin/opencode",
        "run",
        "--dir",
        "/workspace/proj",
        "--auto",
        "--format",
        "json",
    ]
    assert "--model" in cmd
    assert "opencode/mimo-v2.6-flash-free" in cmd
    assert "--session" not in cmd
    assert cmd[-1] == "fix the bug"
    agents = settings.opencode_config_dir / "AGENTS.md"
    assert agents.is_file()
    assert "telegram-cursor-agent" in agents.read_text(encoding="utf-8")
    config = json.loads((settings.opencode_config_dir / "opencode.jsonc").read_text())
    assert config["agent"]["build"]["steps"] == settings.opencode_max_steps


def test_build_command_resumes_opencode_session(test_settings, runner, tmp_workspace) -> None:
    (tmp_workspace / ".cursor_model").write_text("opencode/big-pickle\n", encoding="utf-8")
    settings = test_settings.model_copy(update={"projects_root": tmp_workspace})
    adapter = OpenCodeAgentAdapter(settings, runner)
    cmd = adapter.build_command("/tmp", "next", resume_chat_id="ses_abc")
    assert "--session" in cmd
    assert "ses_abc" in cmd


def test_parse_stream_keeps_only_final_text() -> None:
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses_1",
                    "part": {"type": "text", "text": "[Identity]\nты бот\n\n[User task]\nсделай лендинг"},
                }
            ),
            json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses_1",
                    "part": {"type": "text", "text": "Читаю скиллы."},
                }
            ),
            json.dumps(
                {
                    "type": "tool_use",
                    "sessionID": "ses_1",
                    "part": {
                        "type": "tool",
                        "tool": "read",
                        "state": {
                            "status": "completed",
                            "input": {"filePath": "/root/.cursor/skills/impeccable/SKILL.md"},
                        },
                    },
                }
            ),
            json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses_1",
                    "part": {"type": "text", "text": "Готово. Ссылка ниже."},
                }
            ),
        ]
    )
    events, output, chat_id = _parse_stream_output(raw)
    assert output == "Готово. Ссылка ниже."
    assert chat_id == "ses_1"
    assert events[-1].content == "Готово. Ссылка ниже."
    tool = next(event for event in events if event.event_type == "tool_use")
    assert tool.content.startswith("📚 Скилл: impeccable")


def test_live_steps_match_cursor_card() -> None:
    from telegram_cursor_agent.agent.opencode_adapter import OpenCodeProgress

    progress = OpenCodeProgress(show_tool_calls_live=True)
    first = progress.on_event(
        {"type": "text", "part": {"text": "Читаю скиллы."}}
    )
    assert first is not None
    assert "Читаю скиллы" in first
    with_tool = progress.on_event(
        {
            "type": "tool_use",
            "part": {
                "tool": "bash",
                "state": {"status": "completed", "input": {"command": "nginx -t"}},
            },
        }
    )
    assert with_tool is not None
    assert "Читаю скиллы" in with_tool
    assert "Shell" in with_tool
    assert "nginx" in with_tool
    final = progress.on_event(
        {"type": "text", "part": {"text": "Готово."}}
    )
    assert final is not None
    assert "Готово" in final
    assert "nginx -t" not in final
    assert progress.answer == "Готово."


def test_parse_stream_collects_text_and_session() -> None:
    raw = "\n".join(
        [
            json.dumps(
                {
                    "type": "text",
                    "sessionID": "ses_1",
                    "part": {"type": "text", "text": "PONG"},
                }
            ),
            json.dumps(
                {
                    "type": "step_finish",
                    "sessionID": "ses_1",
                    "part": {"type": "step-finish"},
                }
            ),
        ]
    )
    _events, output, chat_id = _parse_stream_output(raw)
    assert output == "PONG"
    assert chat_id == "ses_1"


def test_running_tool_summaries_from_state_db(tmp_path) -> None:
    import sqlite3

    from telegram_cursor_agent.agent.opencode_adapter import running_tool_summaries

    db_path = tmp_path / "opencode.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE part (id TEXT, session_id TEXT, data TEXT)"
    )
    conn.execute(
        "INSERT INTO part (id, session_id, data) VALUES (?, ?, ?)",
        (
            "prt_1",
            "ses_abc",
            json.dumps(
                {
                    "type": "tool",
                    "tool": "bash",
                    "state": {
                        "status": "running",
                        "input": {"command": "nginx -t"},
                    },
                }
            ),
        ),
    )
    conn.execute(
        "INSERT INTO part (id, session_id, data) VALUES (?, ?, ?)",
        (
            "prt_2",
            "ses_abc",
            json.dumps(
                {
                    "type": "tool",
                    "tool": "read",
                    "state": {"status": "completed", "input": {"filePath": "/etc/hosts"}},
                }
            ),
        ),
    )
    conn.commit()
    conn.close()

    rows = running_tool_summaries(db_path, "ses_abc")
    assert rows == [("prt_1", "🔧 Shell: nginx -t")]


def test_verified_models_are_opencode_family() -> None:
    assert len(OPENCODE_FREE_MODELS) == 8
    for item in OPENCODE_FREE_MODELS:
        assert is_opencode_model(item["id"])
        assert detect_family(item["id"], item["label"]) == ("opencode", "OpenCode")
