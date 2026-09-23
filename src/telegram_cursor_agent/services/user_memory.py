"""Per-user markdown memory files injected into agent prompts."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from telegram_cursor_agent.core.config import Settings

MEMORY_FILE_NAMES: tuple[str, ...] = ("user.md", "soul.md", "memory.md")

_FILE_TEMPLATES: dict[str, str] = {
    "user.md": (
        "# User profile\n\n"
        "Stable preferences: language, timezone, active projects, stack choices.\n"
        "Edit when the user states long-term prefs (not one-off tasks).\n"
    ),
    "soul.md": (
        "# Soul / tone\n\n"
        "Optional: how to address the user, verbosity, boundaries.\n"
        "Leave empty if defaults are fine.\n"
    ),
    "memory.md": (
        "# Memory log\n\n"
        "Append dated bullets when the user says «запомни», «remember», etc.\n"
        "One fact per line; no secrets (tokens, passwords).\n"
    ),
}


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
        "These files survive restarts and new tasks. Paths on disk:",
    ]
    for name, path in paths.items():
        parts.append(f"- `{name}` → `{path}`")
    parts.append("")
    parts.append("Use them as follows:")
    parts.append("- `user.md` — lasting preferences and project context")
    parts.append("- `soul.md` — tone/persona (optional)")
    parts.append("- `memory.md` — dated facts («запомни …»)")
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


def format_memory_status(settings: Settings, telegram_id: int) -> str:
    """Short summary for /memory command."""
    if not settings.user_memory_enabled:
        return "Память отключена (`USER_MEMORY_ENABLED=false`)."

    directory = ensure_memory_files(settings, telegram_id)
    lines = [
        f"Каталог памяти: `{directory}`",
        "",
        "Файлы:",
    ]
    for name in MEMORY_FILE_NAMES:
        path = directory / name
        size = path.stat().st_size if path.is_file() else 0
        lines.append(f"• `{name}` — {size} байт")
    lines.extend(
        [
            "",
            "Скажи «запомни …» в задаче — агент допишет `memory.md`.",
            "Долгие prefs — в `user.md`, тон — в `soul.md`.",
        ]
    )
    return "\n".join(lines)


def append_memory_line(settings: Settings, telegram_id: int, line: str) -> Path:
    """Append one line to memory.md (for future bot shortcuts)."""
    ensure_memory_files(settings, telegram_id)
    path = user_memory_dir(settings, telegram_id) / "memory.md"
    stamp = datetime.now(tz=UTC).strftime("%Y-%m-%d")
    cleaned = line.strip().replace("\n", " ")
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"- {stamp}: {cleaned}\n")
    return path
