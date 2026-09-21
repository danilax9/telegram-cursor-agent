"""Classify Cursor agent failures for account rotation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum


class AgentTimeoutError(Exception):
    """The agent was killed by the task timeout; partial work may still be useful."""

    def __init__(self, seconds: int, partial_output: str = "") -> None:
        super().__init__(f"agent timed out after {seconds}s")
        self.seconds = seconds
        self.partial_output = partial_output


class AgentFailureKind(StrEnum):
    NONE = "none"
    AUTH = "auth"
    LIMIT = "limit"
    OTHER = "other"


_AUTH_PATTERNS = (
    r"unauthorized",
    r"authentication",
    r"not authenticated",
    r"session expired",
    r"invalid token",
    r"login required",
    r"401",
    r"403",
)

_LIMIT_PATTERNS = (
    r"usage limit",
    r"rate limit",
    r"quota",
    r"subscription",
    r"plan limit",
    r"out of credits",
    r"billing",
    r"exceeded",
    r"too many requests",
    r"429",
)


@dataclass
class AgentRunOutcome:
    output: str
    stderr: str = ""
    returncode: int | None = None
    cancelled: bool = False


def classify_agent_result(result: AgentRunOutcome) -> AgentFailureKind:
    """Detect auth or limit failures from agent CLI output."""
    if result.cancelled:
        return AgentFailureKind.OTHER

    combined = "\n".join(
        part for part in (result.output, result.stderr) if part
    ).lower()

    if result.returncode not in (None, 0) and not combined.strip():
        return AgentFailureKind.OTHER

    if _matches_any(combined, _AUTH_PATTERNS):
        return AgentFailureKind.AUTH
    if _matches_any(combined, _LIMIT_PATTERNS):
        return AgentFailureKind.LIMIT

    if result.returncode not in (None, 0):
        return AgentFailureKind.OTHER

    return AgentFailureKind.NONE


def is_account_switchable_failure(kind: AgentFailureKind) -> bool:
    return kind in {AgentFailureKind.AUTH, AgentFailureKind.LIMIT}


def _matches_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)
