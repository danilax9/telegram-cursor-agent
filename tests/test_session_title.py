"""Session title generation tests."""

from telegram_cursor_agent.agent.session_title import generate_session_title


def test_title_is_short_phrase() -> None:
    title = generate_session_title(
        "можешь добавить команду /limits для просмотра лимитов?",
        "Готово. Добавил /limits.",
    )
    assert "—" not in title
    assert len(title.split()) <= 3
    assert "limits" in title.lower()


def test_title_strips_filler_prefix() -> None:
    title = generate_session_title(
        "можешь исправить markdown в telegram",
        "Исправил форматирование сообщений.",
    )
    assert not title.lower().startswith("можешь")
    assert len(title.split()) <= 3


def test_title_supplements_from_assistant_when_user_is_short() -> None:
    title = generate_session_title("/fix", "Chunked stdout reading")
    assert len(title.split()) >= 2
    assert "—" not in title


def test_title_fallback_for_empty_input() -> None:
    assert generate_session_title("", "") == "Новая сессия"
