"""Intent parsing tests."""

from telegram_cursor_agent.agent.parser import IntentType, parse_intent


def test_help_intent() -> None:
    intent = parse_intent("help")
    assert intent.intent == IntentType.HELP
    assert intent.confidence == 1.0


def test_cancel_intent() -> None:
    intent = parse_intent("cancel")
    assert intent.intent == IntentType.CANCEL


def test_git_status_intent() -> None:
    intent = parse_intent("git status")
    assert intent.intent == IntentType.GIT_STATUS


def test_run_command_intent() -> None:
    intent = parse_intent("run ls -la")
    assert intent.intent == IntentType.RUN_COMMAND
    assert intent.payload == "ls -la"


def test_select_project_intent() -> None:
    intent = parse_intent("use project my-app")
    assert intent.intent == IntentType.SELECT_PROJECT
    assert intent.project_name == "my-app"


def test_agent_prompt_fallback() -> None:
    intent = parse_intent("refactor the auth module")
    assert intent.intent == IntentType.AGENT_PROMPT
    assert intent.payload == "refactor the auth module"


def test_unknown_short_text() -> None:
    intent = parse_intent("ab")
    assert intent.intent == IntentType.UNKNOWN


def test_list_projects_intent() -> None:
    intent = parse_intent("projects")
    assert intent.intent == IntentType.LIST_PROJECTS
