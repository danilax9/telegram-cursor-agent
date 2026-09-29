"""Session titles and per-engine resume ids."""

from telegram_cursor_agent.agent.session_titles import (
    resume_id_for_engine,
    saved_chat_kind,
    title_from_cursor_transcript,
    title_from_prompt,
)
from telegram_cursor_agent.core.config import Settings


def test_title_from_prompt_uses_first_line() -> None:
    assert title_from_prompt("почини сессии курсора\nи потом opencode") == (
        "почини сессии курсора"
    )


def test_title_from_prompt_skips_slash_and_identity() -> None:
    assert title_from_prompt("/summarize") == ""
    wrapped = "[Identity]\nты бот\n\n[User task]\nЗапрос: разбери скрин"
    assert title_from_prompt(wrapped) == "разбери скрин"


def test_title_strips_markdown() -> None:
    assert title_from_prompt("сделай *жирный* _заголовок_") == "сделай жирный заголовок"


def test_resume_ids_stay_on_their_engine() -> None:
    assert (
        resume_id_for_engine(
            use_opencode=False,
            cursor_chat_id="634ba530-e36c-4bd9-b247-dbe05879b10e",
            opencode_session_id="ses_abc",
        )
        == "634ba530-e36c-4bd9-b247-dbe05879b10e"
    )
    assert (
        resume_id_for_engine(
            use_opencode=True,
            cursor_chat_id="634ba530-e36c-4bd9-b247-dbe05879b10e",
            opencode_session_id="ses_abc",
        )
        == "ses_abc"
    )
    assert (
        resume_id_for_engine(
            use_opencode=False,
            cursor_chat_id="ses_abc",
            opencode_session_id=None,
        )
        is None
    )
    assert saved_chat_kind("ses_abc") == "opencode"
    assert saved_chat_kind("634ba530-e36c-4bd9-b247-dbe05879b10e") == "cursor"


def test_title_from_transcript(tmp_path, test_settings: Settings) -> None:
    chat_id = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
    folder = (
        tmp_path
        / "main"
        / ".cursor"
        / "projects"
        / "agent-transcripts"
        / chat_id
    )
    folder.mkdir(parents=True)
    (folder / f"{chat_id}.jsonl").write_text(
        '{"role":"user","message":{"content":[{"type":"text","text":"[User task]\\nпроверь antigravity"}]}}\n',
        encoding="utf-8",
    )
    settings = test_settings.model_copy(update={"cursor_accounts_dir": tmp_path})
    assert title_from_cursor_transcript(settings, chat_id) == "проверь antigravity"
