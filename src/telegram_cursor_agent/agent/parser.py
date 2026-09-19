"""Natural language intent parsing via keyword/phrase rules (no huge regex)."""

from dataclasses import dataclass
from enum import StrEnum


class IntentType(StrEnum):
    AGENT_PROMPT = "agent_prompt"
    RUN_COMMAND = "run_command"
    GIT_STATUS = "git_status"
    GIT_DIFF = "git_diff"
    GIT_LOG = "git_log"
    LIST_PROJECTS = "list_projects"
    SELECT_PROJECT = "select_project"
    CANCEL = "cancel"
    HELP = "help"
    STATUS = "status"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ParsedIntent:
    intent: IntentType
    confidence: float
    payload: str = ""
    project_name: str = ""


_HELP_PHRASES = frozenset({"help", "?", "commands", "what can you do"})
_CANCEL_PHRASES = frozenset({"cancel", "stop", "abort", "kill"})
_STATUS_PHRASES = frozenset({"status", "state", "progress"})
_LIST_PROJECTS_PHRASES = frozenset(
    {"list projects", "show projects", "projects", "list repos", "repos"}
)

_GIT_STATUS_PHRASES = frozenset({"git status", "status git", "repo status"})
_GIT_DIFF_PHRASES = frozenset({"git diff", "show diff", "diff"})
_GIT_LOG_PHRASES = frozenset({"git log", "show log", "commit history", "log"})

_SELECT_PREFIXES = ("use project ", "select project ", "switch to ", "project ")


def _normalize(text: str) -> str:
    return text.strip().lower()


def _starts_with_any(text: str, prefixes: tuple[str, ...]) -> str | None:
    for prefix in prefixes:
        if text.startswith(prefix):
            return text[len(prefix) :].strip()
    return None


def parse_intent(text: str) -> ParsedIntent:
    """Parse user text into a typed intent with confidence score."""
    if not text or not text.strip():
        return ParsedIntent(IntentType.UNKNOWN, 0.0)

    normalized = _normalize(text)
    original = text.strip()

    if normalized in _HELP_PHRASES:
        return ParsedIntent(IntentType.HELP, 1.0)

    if normalized in _CANCEL_PHRASES:
        return ParsedIntent(IntentType.CANCEL, 1.0)

    if normalized in _STATUS_PHRASES:
        return ParsedIntent(IntentType.STATUS, 1.0)

    if normalized in _LIST_PROJECTS_PHRASES:
        return ParsedIntent(IntentType.LIST_PROJECTS, 1.0)

    if normalized in _GIT_STATUS_PHRASES:
        return ParsedIntent(IntentType.GIT_STATUS, 0.95)

    if normalized in _GIT_DIFF_PHRASES:
        return ParsedIntent(IntentType.GIT_DIFF, 0.95)

    if normalized in _GIT_LOG_PHRASES:
        return ParsedIntent(IntentType.GIT_LOG, 0.95)

    project_name = _starts_with_any(normalized, _SELECT_PREFIXES)
    if project_name:
        return ParsedIntent(
            IntentType.SELECT_PROJECT, 0.9, project_name=project_name
        )

    if normalized.startswith("run ") or normalized.startswith("exec "):
        cmd = original.split(" ", 1)[1] if " " in original else ""
        return ParsedIntent(IntentType.RUN_COMMAND, 0.85, payload=cmd)

    if normalized.startswith("git "):
        return ParsedIntent(IntentType.RUN_COMMAND, 0.8, payload=original)

    if normalized.startswith("/"):
        return ParsedIntent(IntentType.UNKNOWN, 0.0, payload=original)

    if len(original) >= 3:
        return ParsedIntent(IntentType.AGENT_PROMPT, 0.7, payload=original)

    return ParsedIntent(IntentType.UNKNOWN, 0.3, payload=original)
