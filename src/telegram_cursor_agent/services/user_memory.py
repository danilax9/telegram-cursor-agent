"""Per-user markdown memory files injected into agent prompts."""

from __future__ import annotations

import hashlib
import html
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from telegram_cursor_agent.core.config import Settings

MEMORY_FILE_NAMES: tuple[str, ...] = ("user.md", "soul.md", "memory.md")

MEMORY_AGENT_RULE_MARKDOWN = """---
description: Persistent per-user memory files (user.md, soul.md, memory.md)
alwaysApply: true
---

## Persistent memory (per Telegram user)

Files are **global for the Telegram account** (all projects), not per-repo design notes.
Paths appear in `[Persistent memory]` at the start of task prompts (when enabled).

| File | Purpose |
|------|---------|
| `user.md` | **About the user**: language, timezone, stacks, long-term preferences, stable facts about them |
| `soul.md` | **Tone**: how to address them, verbosity, boundaries, communication style |
| `memory.md` | **Important facts**: dated bullets — explicit «запомни» / «remember», or the same fact **twice** in conversation |

### When to write

**`user.md`** — when you notice a **stable** preference or fact about the user (not a one-off task).
You may update without them saying «запомни», if it clearly belongs in their profile.

**`memory.md`** — when they ask to remember, **or** when they stated the same important fact twice (same meaning).

**`soul.md`** — when they set tone/persona or you infer a stable communication preference they would want kept.

### Rules

1. Keep entries short; dedupe obvious duplicates.
2. **Never** store secrets (tokens, passwords, private keys).
3. Do **not** put project-only prefs here (landing layout, repo details) unless the user says it applies everywhere or asks to remember.
4. `memory.md` lines: `- YYYY-MM-DD: fact`
5. After **you** edit any memory file, add **one short line** in the Telegram reply that you saved it (the bot also sends a separate notice).

The user can run `/memory` in the bot to see file locations and contents.
"""

_FILE_TEMPLATES: dict[str, str] = {
    "user.md": (
        "# User profile\n\n"
        "About you: language, timezone, stacks, long-term preferences.\n"
        "The agent may append stable prefs here when it notices them.\n"
    ),
    "soul.md": (
        "# Soul / tone\n\n"
        "How to address you, verbosity, boundaries.\n"
        "Leave minimal if defaults are fine.\n"
    ),
    "memory.md": (
        "# Memory log\n\n"
        "Dated facts: «запомни …», or repeated twice in chat.\n"
        "One fact per line; no secrets.\n"
    ),
}


@dataclass(frozen=True)
class MemoryFileChange:
    name: str
    preview: str


def resolved_memory_root(settings: Settings) -> Path:
    if settings.user_memory_root is not None:
        return settings.user_memory_root.expanduser().resolve()
    return (settings.projects_root / ".telegram-cursor-agent" / "memory").resolve()


def user_memory_dir(settings: Settings, telegram_id: int) -> Path:
    if telegram_id <= 0:
        msg = "telegram_id must be positive"
        raise ValueError(msg)
    return resolved_memory_root(settings) / str(telegram_id)


def memory_file_paths(settings: Settings, telegram_id: int) -> dict[str, Path]:
    base = user_memory_dir(settings, telegram_id)
    return {name: base / name for name in MEMORY_FILE_NAMES}


def ensure_memory_files(settings: Settings, telegram_id: int) -> Path:
    """Create the user memory directory and template files if missing."""
    directory = user_memory_dir(settings, telegram_id)
    directory.mkdir(parents=True, exist_ok=True)
    for name, template in _FILE_TEMPLATES.items():
        path = directory / name
        if not path.is_file():
            path.write_text(template, encoding="utf-8")
    return directory


def _read_file_tail(path: Path, max_bytes: int) -> str:
    if not path.is_file():
        return ""
    raw = path.read_bytes()
    if len(raw) <= max_bytes:
        return raw.decode("utf-8", errors="replace").strip()
    clipped = raw[-max_bytes:]
    text = clipped.decode("utf-8", errors="replace")
    if "\n" in text:
        text = text.split("\n", 1)[-1]
    return text.strip()


def snapshot_memory_contents(settings: Settings, telegram_id: int) -> dict[str, str]:
    """Full text of each memory file (for before/after diff)."""
    ensure_memory_files(settings, telegram_id)
    paths = memory_file_paths(settings, telegram_id)
    return {
        name: paths[name].read_text(encoding="utf-8")
        if paths[name].is_file()
        else ""
        for name in MEMORY_FILE_NAMES
    }


def _content_fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def memory_content_hashes(contents: dict[str, str]) -> dict[str, str]:
    return {name: _content_fingerprint(contents.get(name, "")) for name in MEMORY_FILE_NAMES}


def detect_memory_changes(
    before: dict[str, str],
    after: dict[str, str],
) -> list[MemoryFileChange]:
    """List files whose content changed between snapshots."""
    changes: list[MemoryFileChange] = []
    for name in MEMORY_FILE_NAMES:
        prev = before.get(name, "")
        curr = after.get(name, "")
        if _content_fingerprint(prev) == _content_fingerprint(curr):
            continue
        before_lines = prev.splitlines()
        added = [line for line in curr.splitlines() if line not in before_lines]
        meaningful = [ln for ln in added if ln.strip() and not ln.startswith("#")]
        if meaningful:
            preview = "\n".join(meaningful[-4:])
        else:
            preview = "(файл изменён)"
        changes.append(MemoryFileChange(name=name, preview=preview))
    return changes


_MEMORY_DISPLAY_MAX_CHARS = 1200


def _escape_rich_html(text: str) -> str:
    return html.escape(text, quote=False)


def _clip_memory_body(body: str, max_chars: int) -> str:
    stripped = body.strip()
    if not stripped:
        return "(пусто)"
    if len(stripped) <= max_chars:
        return stripped
    return stripped[: max_chars - 1] + "…"


def _markdown_blockquote(body: str) -> str:
    lines = body.splitlines()
    return "\n".join(f"> {line}" if line.strip() else ">" for line in lines)


def _format_memory_file_section(
    name: str,
    body: str,
    *,
    rich_html: bool,
    max_chars: int = _MEMORY_DISPLAY_MAX_CHARS,
) -> str:
    display = _clip_memory_body(body, max_chars)
    if rich_html:
        header = f"<b>{_escape_rich_html(name)}</b>"
        inner = _escape_rich_html(display).replace("\n", "<br>")
        return f"{header}\n<blockquote expandable>{inner}</blockquote>"
    return f"**{name}**\n\n{_markdown_blockquote(display)}"


def format_memory_change_notification(change: MemoryFileChange) -> str:
    """Telegram Markdown: emoji header + blockquote body."""
    body = change.preview.strip()
    if len(body) > 500:
        body = body[:497] + "…"
    quoted = "\n".join(
        f"> {line}" if line.strip() else ">" for line in body.splitlines()
    )
    if not quoted:
        quoted = "> (обновлено)"
    return f"🧠 `{change.name}`\n\n{quoted}"


def load_memory_prompt_section(
    settings: Settings,
    telegram_id: int | None,
) -> str:
    """Build the [Persistent memory] block for compose_task_prompt."""
    if telegram_id is None or not settings.user_memory_enabled:
        return ""

    ensure_memory_files(settings, telegram_id)
    paths = memory_file_paths(settings, telegram_id)
    per_file_budget = max(512, settings.user_memory_max_prompt_bytes // len(MEMORY_FILE_NAMES))

    parts: list[str] = [
        "[Persistent memory]",
        "Global for your Telegram account (all projects). Paths on disk:",
    ]
    for name, path in paths.items():
        parts.append(f"- `{name}` → `{path}`")
    parts.append("")
    parts.append("Use them as follows:")
    parts.append("- `user.md` — about the user; stable prefs (agent may auto-save)")
    parts.append("- `soul.md` — tone and how to communicate")
    parts.append("- `memory.md` — important facts («запомни» or said twice)")
    parts.append("")

    total = len("\n".join(parts))
    for name in MEMORY_FILE_NAMES:
        path = paths[name]
        body = _read_file_tail(path, per_file_budget)
        if not body:
            continue
        chunk = f"--- {name} ---\n{body}\n"
        if total + len(chunk) > settings.user_memory_max_prompt_bytes:
            parts.append(f"(… `{name}` truncated in prompt; open file on disk)\n")
            break
        parts.append(chunk)
        total += len(chunk)

    return "\n".join(parts).strip() + "\n\n"


def format_memory_status(
    settings: Settings,
    telegram_id: int,
    *,
    rich_html: bool | None = None,
) -> tuple[str, bool]:
    """Text for /memory and whether to send as Rich Message HTML."""
    if not settings.user_memory_enabled:
        return ("Память отключена (`USER_MEMORY_ENABLED=false`).", False)

    use_html = (
        rich_html if rich_html is not None else settings.telegram_uses_rich_messages
    )
    ensure_memory_files(settings, telegram_id)
    contents = snapshot_memory_contents(settings, telegram_id)

    sections = [
        _format_memory_file_section(
            name,
            contents.get(name, ""),
            rich_html=use_html,
        )
        for name in MEMORY_FILE_NAMES
    ]
    return ("\n\n".join(sections), use_html)


def append_memory_line(settings: Settings, telegram_id: int, line: str) -> Path:
    """Append one line to memory.md (for future bot shortcuts)."""
    ensure_memory_files(settings, telegram_id)
    path = user_memory_dir(settings, telegram_id) / "memory.md"
    stamp = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    cleaned = line.strip().replace("\n", " ")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"- {stamp}: {cleaned}\n")
    return path
