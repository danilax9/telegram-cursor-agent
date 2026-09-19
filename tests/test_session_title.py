"""Session title generation tests."""

from telegram_cursor_agent.agent.session_title import generate_session_title


def test_title_from_user_and_assistant() -> None:
    title = generate_session_title(
        "можешь добавить команду /limits для просмотра лимитов?",
        "**Готово.** Добавил `/limits` — команда показывает usage Cursor.",
    )
    assert "limits" in title.lower() or "лимит" in title.lower()
    assert len(title) <= 64


def test_title_strips_filler_prefix() -> None:
    title = generate_session_title(
        "можешь исправить markdown в telegram",
        "Исправил форматирование сообщений.",
    )
    assert not title.lower().startswith("можешь")
    assert "markdown" in title.lower() or "исправ" in title.lower()


def test_title_uses_assistant_heading() -> None:
    title = generate_session_title(
        "сделай рефакторинг runner.py",
        "## Chunked stdout reading\n\nЗаменил readline на chunked reader.",
    )
    assert "chunked" in title.lower() or "stdout" in title.lower()


def test_title_fallback_for_empty_input() -> None:
    assert generate_session_title("", "") == "Новая сессия"
