"""Instructions and step budget OpenCode reads from its own config."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from telegram_cursor_agent.agent.prompts import (
    SELF_DEPLOY_RULE_CONTENT,
    USER_MEMORY_RULE_CONTENT,
    build_telegram_rule_content,
)
from telegram_cursor_agent.agent.skills import build_skills_routing_rule
from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.services.user_memory import load_memory_prompt_section

_PACE = """
## Pace

The user is waiting in Telegram. Finish the task, then answer. Do not keep exploring.

- Do not retry a failed or hung tool more than once.
- Do not split screenshots into many crops or call a vision model in a loop unless the user asked for a visual audit.
- When the step budget is reached, stop and answer with what you already know.
"""


def sync_opencode_context(
    settings: Settings,
    workspace: str,
    telegram_id: int | None,
) -> None:
    """Write AGENTS.md and the agent step cap into the OpenCode config dir."""
    config_dir = settings.opencode_config_dir
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "AGENTS.md").write_text(
        _agents_markdown(settings, workspace, telegram_id),
        encoding="utf-8",
    )
    _ensure_step_limit(config_dir / "opencode.jsonc", settings.opencode_max_steps)


def _agents_markdown(
    settings: Settings,
    workspace: str,
    telegram_id: int | None,
) -> str:
    sections = [
        "# Telegram Cursor agent\n\n"
        "These instructions are loaded by OpenCode from its config. "
        "The user message is only the task, not a second copy of this file.\n",
        build_telegram_rule_content(
            rich_messages=settings.telegram_uses_rich_messages
        ),
        build_skills_routing_rule(settings, workspace),
        _PACE,
    ]
    if settings.user_memory_enabled:
        sections.append(USER_MEMORY_RULE_CONTENT)
        memory = load_memory_prompt_section(settings, telegram_id)
        if memory:
            sections.append(memory)
    if settings.self_deploy_enabled:
        sections.append(SELF_DEPLOY_RULE_CONTENT)
    return "\n\n".join(section.strip() for section in sections if section.strip()) + "\n"


def _ensure_step_limit(path: Path, steps: int) -> None:
    raw: dict[str, Any] = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if isinstance(loaded, dict):
            raw = loaded
    agent = raw.get("agent")
    if not isinstance(agent, dict):
        agent = {}
        raw["agent"] = agent
    build = agent.get("build")
    if not isinstance(build, dict):
        build = {}
        agent["build"] = build
    if build.get("steps") == steps:
        return
    build["steps"] = steps
    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
