"""Command classification and permission checks."""

from dataclasses import dataclass
from enum import StrEnum

from telegram_cursor_agent.core.config import Settings


class CommandRisk(StrEnum):
    SAFE = "safe"
    SENSITIVE = "sensitive"
    FORBIDDEN = "forbidden"


@dataclass(frozen=True)
class CommandClassification:
    risk: CommandRisk
    command: str
    reason: str = ""


_FORBIDDEN_SUBSTRINGS = (
    "rm -rf",
    "rm -r",
    "mkfs",
    "dd if=",
    ":(){",
    "chmod 777",
    "curl | sh",
    "wget | sh",
    "> /dev/",
    "shutdown",
    "reboot",
    "kill -9 1",
    "DROP TABLE",
    "DROP DATABASE",
)

_SENSITIVE_PREFIXES = (
    "git push",
    "git reset --hard",
    "git clean",
    "git checkout",
    "git merge",
    "git rebase",
    "pip install",
    "npm install",
    "docker",
    "kubectl",
)


def classify_command(command: str, settings: Settings) -> CommandClassification:
    normalized = command.strip()
    if not normalized:
        return CommandClassification(CommandRisk.FORBIDDEN, command, "Empty command")

    lower = normalized.lower()
    for forbidden in _FORBIDDEN_SUBSTRINGS:
        if forbidden.lower() in lower:
            return CommandClassification(
                CommandRisk.FORBIDDEN, command, f"Contains forbidden pattern: {forbidden}"
            )

    first_token = normalized.split()[0]
    base_cmd = first_token.split("/")[-1]
    if base_cmd not in settings.allowed_command_prefixes:
        return CommandClassification(
            CommandRisk.FORBIDDEN,
            command,
            f"Command '{base_cmd}' not in allowed prefixes",
        )

    for sensitive in _SENSITIVE_PREFIXES:
        if lower.startswith(sensitive):
            return CommandClassification(
                CommandRisk.SENSITIVE, command, f"Sensitive command: {sensitive}"
            )

    return CommandClassification(CommandRisk.SAFE, command)


def requires_confirmation(classification: CommandClassification) -> bool:
    return classification.risk == CommandRisk.SENSITIVE
