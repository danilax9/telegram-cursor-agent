"""Session title prompt and normalization tests."""

from telegram_cursor_agent.agent.session_title import (
    DEFAULT_SESSION_TITLE,
    format_title_prompt,
    normalize_session_title,
)


def test_format_title_prompt_includes_exchange() -> None:
    prompt = format_title_prompt("добавь /limits", "Команда готова")
    assert "добавь /limits" in prompt
    assert "Команда готова" in prompt
    assert "2-3 words" in prompt


def test_normalize_session_title_strips_quotes() -> None:
    assert normalize_session_title('"Limits command"') == "Limits command"


def test_normalize_session_title_limits_words() -> None:
    assert normalize_session_title("Fix telegram markdown formatting now") == (
        "Fix telegram markdown"
    )


def test_normalize_session_title_empty_returns_empty() -> None:
    assert normalize_session_title("   ") == ""


def test_default_session_title_constant() -> None:
    assert DEFAULT_SESSION_TITLE == "Новая сессия"
