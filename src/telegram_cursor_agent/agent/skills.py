"""Discover Cursor Agent Skills and build routing rules for the Telegram agent."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from telegram_cursor_agent.core.config import Settings

_FRONTMATTER_FIELD = re.compile(
    r"^([a-zA-Z0-9_-]+):\s*(.+?)\s*$",
    re.MULTILINE,
)


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


_FOREIGN_SKILL_PARTS = frozenset({".hermes", "skills-cursor"})
_MAX_AUTO_SKILLS = 3
_MAX_INLINE_BODY = 4000

# Russian and English task phrases → skill names this agent actually installs.
# First match wins the slots; exact skill names in the prompt are added before this.
_SKILL_ROUTES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("скилл*", "skill*"), ("skill-manager",)),
    (
        ("фигма*", "figma*"),
        ("figma-design-to-code", "figma-code-connect", "figma-implement-motion"),
    ),
    (
        ("лендинг*", "landing*", "посадочн*", "сайт*"),
        ("web-landing-pages", "modern-web-design", "frontend-design"),
    ),
    (
        ("checkout", "оплат*", "форма", "формы", "форму", "форме"),
        ("forms-inputs-checkout",),
    ),
    (
        ("доступн*", "a11y", "accessibility", "инклюз*"),
        ("accessibility-inclusive-design", "web-ux-a11y"),
    ),
    (
        ("навигац*", "information architecture"),
        ("information-architecture-navigation",),
    ),
    (
        ("дизайн-систем*", "design system", "design-system"),
        ("design-systems-frontend-architecture",),
    ),
    (
        ("tailwind", "shadcn", "next.js", "nextjs"),
        ("frontend-ui-stack", "frontend-design"),
    ),
    (
        ("моушн*", "motion", "анимац*"),
        ("figma-implement-motion", "interaction-patterns-components"),
    ),
    (("микротекст*", "ux writing", "копирайт*"), ("ux-writing-content-design",)),
    (("юзабил*", "usability"), ("ux-usability-foundations",)),
    (
        ("интерфейс*", "верст*", "ui/ux", "дизайн*"),
        ("modern-web-design", "frontend-design", "web-ux-a11y"),
    ),
)

_TOKEN_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "agent",
        "before",
        "could",
        "cursor",
        "other",
        "should",
        "skill",
        "skills",
        "telegram",
        "their",
        "there",
        "these",
        "those",
        "using",
        "which",
        "would",
    }
)


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


def _normalize_task_text(text: str) -> str:
    return text.casefold().replace("ё", "е")


def _phrase_in(text: str, phrase: str) -> bool:
    """Match a route phrase. A trailing * means 'prefix of a word' (скилл* → скиллы)."""
    prefix = phrase.endswith("*")
    raw = phrase[:-1] if prefix else phrase
    needle = _normalize_task_text(raw)
    if any(char in needle for char in " -./"):
        return needle in text
    if prefix:
        pattern = rf"(?<![a-zа-яе0-9]){re.escape(needle)}"
    else:
        pattern = rf"(?<![a-zа-яе0-9]){re.escape(needle)}(?![a-zа-яе0-9])"
    return re.search(pattern, text) is not None


def _score_skill(skill: CursorSkill, text: str) -> int:
    haystack = _normalize_task_text(f"{skill.name} {skill.description}")
    tokens = {
        token
        for token in re.findall(r"[a-zа-яе]{5,}", haystack)
        if token not in _TOKEN_STOPWORDS
    }
    prompt_tokens = set(re.findall(r"[a-zа-яе]{5,}", text))
    return len(tokens & prompt_tokens)


def select_skills_for_prompt(skills: list[CursorSkill], prompt: str) -> list[CursorSkill]:
    """Pick at most a few installed skills that match this task. Never invents paths."""
    text = _normalize_task_text(prompt)
    by_name = {skill.name.casefold(): skill for skill in skills}
    chosen: list[CursorSkill] = []

    def add(name: str) -> None:
        skill = by_name.get(name.casefold())
        if skill is None or skill in chosen or len(chosen) >= _MAX_AUTO_SKILLS:
            return
        chosen.append(skill)

    for skill in skills:
        if skill.name.casefold() in text:
            add(skill.name)
    for phrases, names in _SKILL_ROUTES:
        if any(_phrase_in(text, phrase) for phrase in phrases):
            for name in names:
                add(name)
    if not chosen:
        ranked = sorted(
            ((skill, _score_skill(skill, text)) for skill in skills),
            key=lambda item: (-item[1], item[0].name),
        )
        for skill, score in ranked:
            if score < 2:
                break
            add(skill.name)
    return chosen


def _skill_body(path: Path, skills_root: Path | None = None) -> str:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :]
    body = text.strip()
    if skills_root is not None:
        body = body.replace("~/.cursor/skills", str(skills_root))
    return body


def _primary_skills_root(settings: Settings) -> Path | None:
    for item in settings.cursor_skills_dirs:
        path = Path(item).expanduser()
        if not _is_foreign_skill_path(path):
            return path
    return None


def _format_required_skills(
    selected: list[CursorSkill],
    skills_root: Path | None = None,
) -> str:
    if not selected:
        return (
            "Скилл для этой задачи не выбран. Не ищи скиллы в чужих каталогах. "
            "Если дальше станет ясно, что задача совпадает с именем из списка выше, "
            "сначала прочитай его SKILL.md и только потом меняй файлы."
        )
    lines = ["Обязательные скиллы — прочитай и выполни до любых других действий:"]
    pending_read: list[str] = []
    inlined = 0
    blocked = False
    for skill in selected:
        lines.append(f"- `{skill.name}` — `{skill.skill_md}`")
        body = _skill_body(skill.skill_md, skills_root)
        fits = bool(body) and len(body) <= _MAX_INLINE_BODY
        if not blocked and inlined < 1 and fits:
            lines.append(f"Инструкция `{skill.name}`:")
            lines.append(body)
            inlined += 1
            continue
        if not fits:
            blocked = True
        pending_read.append(skill.name)
    if pending_read:
        names = ", ".join(f"`{name}`" for name in pending_read)
        lines.append(f"До правок открой Read для: {names}.")
    return "\n".join(lines)


def compose_task_prompt(settings: Settings, workspace: str, prompt: str) -> str:
    """Identity plus the skills that apply. System recovery notes stay untouched."""
    stripped = prompt.lstrip()
    if stripped.startswith("[Identity]"):
        return prompt
    if stripped.startswith("[System:") and "инструкция" not in prompt.casefold():
        return prompt

    skills = discover_cursor_skills(settings, workspace)
    selected = select_skills_for_prompt(skills, prompt)
    skills_root = _primary_skills_root(settings)
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
        f"{_format_required_skills(selected, skills_root)}\n\n"
        "[User task]\n"
    )
    return f"{header}{prompt}"


def build_skills_routing_rule(settings: Settings, workspace: str) -> str:
    """Always-on rule: how to route tasks to Cursor Agent Skills."""
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
description: Route tasks to Cursor Agent Skills (SKILL.md) — mandatory before substantive work
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
A skill is yours only if its path is in this index or in the task's required-skill list.
The task prompt already names required skills — Read those paths before other work.

### Mandatory routing (every task)

1. Match the user message and planned work against the **skill index**
   below (names + descriptions).
2. If **any** skill applies even partially, **read the full `SKILL.md`**
   with the Read tool **before** editing code, running deploys, or
   destructive shell commands.
3. Follow loaded skill instructions for the rest of the task.
   Combine multiple skills when the task spans domains
   (for example landing page → `web-landing-pages` + `modern-web-design` + `web-ux-a11y`).
4. For **glob-triggered** frontend work, also follow `modern-web.mdc` in this workspace.
5. When the user asks to **install, update, list, or remove** skills
   (`добавь скилл`, `установи skill`, `skill-manager`), read and follow
   **`skill-manager`** first (`/root/.cursor/skills/skill-manager/SKILL.md`).

### Installed skills index (refreshed each agent run)

{skills_index}
"""
