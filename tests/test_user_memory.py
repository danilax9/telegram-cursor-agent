"""Per-user markdown memory files."""

from pathlib import Path

import pytest

from telegram_cursor_agent.agent.skills import compose_task_prompt
from telegram_cursor_agent.services.user_memory import (
    append_memory_line,
    ensure_memory_files,
    format_memory_status,
    load_memory_prompt_section,
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


def test_format_memory_status(test_settings) -> None:
    text = format_memory_status(test_settings, 999005)
    assert "user.md" in text
