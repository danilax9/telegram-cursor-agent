"""Discover Cursor Agent Skills and sync routing rules for the Telegram agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.services.user_memory import load_memory_prompt_section

_FRONTMATTER_FIELD = re.compile(
    r"^([a-zA-Z0-9_-]+):\s*(.+?)\s*$",
    re.MULTILINE,
)

_FOREIGN_SKILL_PARTS = frozenset({".hermes", "skills-cursor"})


@dataclass(frozen=True, slots=True)
class CursorSkill:
    name: str
    description: str
    skill_md: Path
    scope: str


def _parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    block = text[3:end].strip()
    fields: dict[str, str] = {}
    for match in _FRONTMATTER_FIELD.finditer(block):
        key = match.group(1).strip().lower()
        value = match.group(2).strip().strip('"').strip("'")
        fields[key] = value
    return fields


def _is_foreign_skill_path(path: Path) -> bool:
    return bool(_FOREIGN_SKILL_PARTS.intersection(path.parts))


def _logical_skill_path(skill_md: Path) -> Path:
    """Absolute path that keeps symlinks, so plugin-cache targets stay hidden."""
    path = skill_md.expanduser()
    if path.is_absolute():
        return path
    return path.absolute()


def _read_skill(skill_md: Path, scope: str) -> CursorSkill | None:
    path = _logical_skill_path(skill_md)
    if _is_foreign_skill_path(path):
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    meta = _parse_frontmatter(text)
    name = meta.get("name") or path.parent.name
    description = meta.get("description", "").strip()
    if not description:
        description = f"Skill at {path}"
    return CursorSkill(
        name=name,
        description=description,
        skill_md=path,
        scope=scope,
    )


def discover_cursor_skills(
    settings: Settings,
    workspace: str | Path | None = None,
) -> list[CursorSkill]:
    """List installed skills from configured dirs and optional project workspace."""
    found: dict[str, CursorSkill] = {}
    for root in settings.cursor_skills_dirs:
        root_path = Path(root).expanduser()
        if _is_foreign_skill_path(root_path) or not root_path.is_dir():
            continue
        for skill_md in sorted(root_path.glob("*/SKILL.md")):
            skill = _read_skill(skill_md, scope="user")
            if skill is not None:
                found[skill.name] = skill

    if workspace:
        ws = Path(workspace).expanduser()
        project_skills = ws / ".cursor" / "skills"
        if project_skills.is_dir() and not _is_foreign_skill_path(project_skills):
            for skill_md in sorted(project_skills.glob("*/SKILL.md")):
                skill = _read_skill(skill_md, scope="project")
                if skill is not None:
                    found[skill.name] = skill

    return sorted(found.values(), key=lambda item: item.name)


def format_skills_index(skills: list[CursorSkill]) -> str:
    if not skills:
        return (
            "_No skills found under configured dirs. "
            "Use `skill-manager` after installing skills to `~/.cursor/skills/`._"
        )
    lines: list[str] = []
    for skill in skills:
        lines.append(
            f"- **{skill.name}** ({skill.scope}) — {skill.description}\n"
            f"  Path: `{skill.skill_md}`"
        )
    return "\n".join(lines)


def _format_skill_selection_mandate() -> str:
    return (
        "### Выбор скиллов (решает агент, не бот)\n\n"
        "Бот **не** подбирает скиллы скриптом и **не** встраивает SKILL.md в промпт.\n"
        "1. Открой `.cursor/rules/skills-routing.mdc` в активном workspace — там актуальный **индекс** "
        "(имя + description + путь).\n"
        "2. Сопоставь задачу пользователя с descriptions; подключи **все** релевантные скиллы "
        "(для лендинга часто несколько: project `saas-product-landing`, `web-landing-pages`, "
        "`ui-design-brain`, `modern-web-design`, `frontend-design`, …).\n"
        "3. Перед правками кода — **Read** полный `SKILL.md` каждого выбранного скилла; "
        "при необходимости вспомогательные файлы в той же папке (например `components.md`).\n"
        "4. Не открывай Hermes, skills-cursor, plugin cache — только пути из индекса.\n"
        "5. Для веб-UI после деплоя проверь живой URL; generic AI-slop / «блог 2010» = переделать."
    )


def compose_task_prompt(
    settings: Settings,
    workspace: str,
    prompt: str,
    *,
    telegram_id: int | None = None,
) -> str:
    """Identity + agent-driven skill mandate. System recovery notes stay untouched."""
    stripped = prompt.lstrip()
    if stripped.startswith("[Identity]"):
        return prompt
    if stripped.startswith("[System:") and "инструкция" not in prompt.casefold():
        return prompt

    skills = discover_cursor_skills(settings, workspace)
    dirs = ", ".join(f"`{Path(item).expanduser()}`" for item in settings.cursor_skills_dirs)
    if not dirs:
        dirs = "`~/.cursor/skills`"
    names = ", ".join(f"`{skill.name}`" for skill in skills) or "(нет установленных)"
    header = (
        "[Identity]\n"
        "Ты telegram-cursor-agent: Cursor-агент на этом сервере, доступный только через Telegram. "
        "Ты не Hermes, не десктопный Cursor IDE и не агент плагина.\n"
        f"Твои скиллы — только Cursor Agent Skills в каталогах {dirs} "
        "и в `.cursor/skills` активного проекта.\n"
        "Фразы «твои скиллы», «кто ты» и «your skills» относятся к этому списку. "
        "Не открывай `~/.hermes/skills`, `~/.cursor/skills-cursor` и кэши плагинов "
        "(`plugins/cache`, `skills-cursor`).\n"
        "Пути скиллов абсолютные. `~` в процессе Cursor — дом аккаунта, не каталог скиллов.\n"
        f"Установленные скиллы: {names}\n\n"
        f"{_format_skill_selection_mandate()}\n\n"
    )
    memory_block = load_memory_prompt_section(settings, telegram_id)
    if memory_block:
        header += memory_block
    header += "[User task]\n"
    return f"{header}{prompt}"


def build_skills_routing_rule(settings: Settings, workspace: str) -> str:
    """Always-on rule: index + how the agent picks skills (no bot-side preselection)."""
    skills = discover_cursor_skills(settings, workspace)
    index = format_skills_index(skills)
    skills_dirs = ", ".join(
        f"`{Path(p).expanduser()}`" for p in settings.cursor_skills_dirs
    )
    return SKILLS_ROUTING_RULE_TEMPLATE.format(
        skills_dirs=skills_dirs,
        skills_index=index,
    )


SKILLS_ROUTING_RULE_TEMPLATE = """---
description: Cursor Agent Skills — agent picks skills from index before substantive work
alwaysApply: true
---

## Cursor Agent Skills (Telegram Cursor agent)

You are **telegram-cursor-agent** on a remote Linux server, reached only via Telegram.
You are not Hermes, not the Cursor desktop IDE, and not a plugin agent.
Operational rules live in `.cursor/rules/`.
**Domain expertise** lives only in **Cursor Agent Skills** — folders with `SKILL.md`
under {skills_dirs} and optionally `<project>/.cursor/skills/`.

«Твои скиллы» means the index below.
Never open `~/.hermes/skills`, `~/.cursor/skills-cursor`, or plugin caches (`plugins/cache`).
A skill is yours only if its path appears in this index.

The Telegram bot does **not** auto-select skills. **You** choose what applies from names and descriptions.

### Mandatory routing (every task)

1. Read the user message and your planned work; scan the **skill index** below.
2. For **each** skill that applies even partially, **Read the full `SKILL.md`**
   with the Read tool **before** editing code, running deploys, or destructive shell commands.
3. Combine multiple skills when the task spans domains
   (landing page → project `saas-product-landing` if present, plus `web-landing-pages`,
   `ui-design-brain`, `modern-web-design`, `frontend-design`, `web-ux-a11y` as needed).
4. When the user asks to **install, update, list, or remove** skills
   (`добавь скилл`, `установи skill`, `skill-manager`), read and follow
   **`skill-manager`** (`SKILL.md` path in the index).

### Installed skills index (refreshed each agent run)

{skills_index}
"""
