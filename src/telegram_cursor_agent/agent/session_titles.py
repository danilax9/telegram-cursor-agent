"""Human titles for agent sessions and which engine owns a chat id."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from telegram_cursor_agent.core.config import Settings

_TITLE_LIMIT = 42
_CHAT_ID = re.compile(r"^[0-9a-fA-F-]{36}$")


def title_from_prompt(prompt: str, *, limit: int = _TITLE_LIMIT) -> str:
    """First meaningful line of a user message, safe for Telegram buttons."""
    text = prompt.strip()
    if "[User task]" in text:
        text = text.split("[User task]", 1)[1].strip()

    chosen = ""
    for raw in text.splitlines():
        line = " ".join(raw.split()).strip()
        if not line or line.startswith("#") or line.startswith("```") or line.startswith("["):
            continue
        if line.lower().startswith("запрос:"):
            line = line.split(":", 1)[1].strip()
        if line.startswith("/") and " " not in line:
            return ""
        chosen = line
        break
    return _clip_title(chosen, limit)


def resume_id_for_engine(
    *,
    use_opencode: bool,
    cursor_chat_id: str | None,
    opencode_session_id: str | None,
) -> str | None:
    """Chat id the active engine can actually resume."""
    if use_opencode:
        if opencode_session_id and opencode_session_id.startswith("ses_"):
            return opencode_session_id
        return None
    if cursor_chat_id and not cursor_chat_id.startswith("ses_"):
        return cursor_chat_id
    return None


def saved_chat_kind(chat_id: str) -> str:
    """`opencode` for OpenCode session ids, otherwise `cursor`."""
    if chat_id.startswith("ses_"):
        return "opencode"
    return "cursor"


def title_from_cursor_transcript(settings: Settings, chat_id: str) -> str:
    """Read the first user task from a Cursor agent transcript, if it exists."""
    if not chat_id or not _CHAT_ID.fullmatch(chat_id):
        return ""
    for path in _transcript_paths(settings, chat_id):
        title = _title_from_transcript_file(path)
        if title:
            return title
    return ""


def _clip_title(text: str, limit: int) -> str:
    cleaned = (
        text.replace("*", "")
        .replace("_", "")
        .replace("`", "'")
        .replace("[", "(")
        .replace("]", ")")
        .strip()
    )
    if not cleaned:
        return ""
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _transcript_paths(settings: Settings, chat_id: str) -> list[Path]:
    filename = f"{chat_id}.jsonl"
    found: list[Path] = []
    home_projects = Path.home() / ".cursor" / "projects"
    if home_projects.is_dir():
        direct = home_projects / "agent-transcripts" / chat_id / filename
        if direct.is_file():
            found.append(direct)
        found.extend(home_projects.glob(f"*/agent-transcripts/{chat_id}/{filename}"))

    accounts = settings.cursor_accounts_dir
    if accounts.is_dir():
        for projects in accounts.glob("*/.cursor/projects"):
            direct = projects / "agent-transcripts" / chat_id / filename
            if direct.is_file():
                found.append(direct)
            found.extend(projects.glob(f"*/agent-transcripts/{chat_id}/{filename}"))
    # de-dupe, keep order
    unique: list[Path] = []
    seen: set[Path] = set()
    for path in found:
        if path in seen or not path.is_file():
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _title_from_transcript_file(path: Path) -> str:
    try:
        blob = path.read_bytes()[:120_000]
    except OSError:
        return ""
    text = blob.decode("utf-8", errors="ignore")
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict) or payload.get("role") != "user":
            continue
        message = payload.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        pieces: list[str] = []
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    pieces.append(str(part.get("text") or ""))
        elif isinstance(content, str):
            pieces.append(content)
        title = title_from_prompt("\n".join(pieces))
        if title:
            return title
    return ""
