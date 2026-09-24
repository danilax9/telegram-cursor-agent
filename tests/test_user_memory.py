"""Per-user markdown memory files."""

from pathlib import Path

import pytest

from telegram_cursor_agent.agent.skills import compose_task_prompt
from telegram_cursor_agent.services.user_memory import (
    append_memory_line,
    detect_memory_changes,
    ensure_memory_files,
    format_memory_change_notification,
    format_memory_status,
    load_memory_prompt_section,
    snapshot_memory_contents,
    user_memory_dir,
)


def test_ensure_memory_files_creates_templates(test_settings) -> None:
    directory = ensure_memory_files(test_settings, telegram_id=999001)
    assert directory.is_dir()
    for name in ("user.md", "soul.md", "memory.md"):
        assert (directory / name).is_file()


def test_load_memory_prompt_includes_paths(test_settings) -> None:
    block = load_memory_prompt_section(test_settings, 999002)
    assert "[Persistent memory]" in block
    assert "user.md" in block
    assert str(user_memory_dir(test_settings, 999002)) in block


def test_compose_task_prompt_injects_memory(test_settings, tmp_path: Path) -> None:
    tid = 999003
    memory_dir = ensure_memory_files(test_settings, tid)
    (memory_dir / "memory.md").write_text(
        "# Memory log\n\n- 2026-01-01: prefers Russian\n",
        encoding="utf-8",
    )
    prompt = compose_task_prompt(
        test_settings, str(tmp_path), "hello", telegram_id=tid
    )
    assert "[Persistent memory]" in prompt
    assert "prefers Russian" in prompt
    assert "[User task]" in prompt


def test_append_memory_line(test_settings) -> None:
    append_memory_line(test_settings, 999004, "test fact")
    path = user_memory_dir(test_settings, 999004) / "memory.md"
    text = path.read_text(encoding="utf-8")
    assert "test fact" in text


def test_format_memory_status_legacy_markdown(test_settings) -> None:
    text, use_html = format_memory_status(
        test_settings, 999005, rich_html=False
    )
    assert use_html is False
    assert "user.md" in text
    assert "blockquote" not in text
    assert "> " in text


def test_format_memory_status_rich_html(test_settings) -> None:
    tid = 999007
    memory_dir = ensure_memory_files(test_settings, tid)
    (memory_dir / "user.md").write_text(
        "# User profile\n\n- Возраст: 23\n",
        encoding="utf-8",
    )
    text, use_html = format_memory_status(test_settings, tid, rich_html=True)
    assert use_html is True
    assert "<blockquote expandable>" in text
    assert "Возраст: 23" in text
    assert "user.md" in text


def test_detect_memory_changes(test_settings) -> None:
    tid = 999006
    before = snapshot_memory_contents(test_settings, tid)
    path = user_memory_dir(test_settings, tid) / "user.md"
    path.write_text(before["user.md"] + "\n- prefers Python\n", encoding="utf-8")
    after = snapshot_memory_contents(test_settings, tid)
    changes = detect_memory_changes(before, after)
    assert len(changes) == 1
    assert changes[0].name == "user.md"
    msg = format_memory_change_notification(changes[0])
    assert "🧠" in msg
    assert "`user.md`" in msg
    assert "> " in msg
    assert "Python" in msg
