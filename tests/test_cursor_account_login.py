"""Cursor in-bot login helpers."""

from telegram_cursor_agent.services.cursor_account_login import (
    ACCOUNT_ID_PATTERN,
    LOGIN_URL_PATTERN,
    CursorAccountLoginService,
)


def test_account_id_pattern() -> None:
    assert ACCOUNT_ID_PATTERN.fullmatch("backup")
    assert ACCOUNT_ID_PATTERN.fullmatch("acc_2")
    assert not ACCOUNT_ID_PATTERN.fullmatch("Bad")


def test_extract_login_url() -> None:
    text = (
        "Waiting for browser authentication...\n"
        "Open a browser and navigate to this link: "
        "https://cursor.com/loginDeepControl?challenge=abc&uuid=def&mode=login&redirectTarget=cli\n"
    )
    match = LOGIN_URL_PATTERN.search(text)
    assert match is not None
    assert match.group(0).startswith("https://cursor.com/loginDeepControl?")


def test_validate_account_id() -> None:
    CursorAccountLoginService.validate_account_id("backup")
    try:
        CursorAccountLoginService.validate_account_id("")
    except Exception as exc:
        assert "Id аккаунта" in str(exc)
        assert "_" not in str(exc)
