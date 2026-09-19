"""Security utility tests."""

from telegram_cursor_agent.core.config import Settings
from telegram_cursor_agent.core.security import (
    is_admin,
    markdown_to_telegram_html,
    redact_secrets,
    require_admin,
    sanitize_for_telegram,
    split_telegram_message,
    truncate_output,
)


def test_is_admin(test_settings: Settings) -> None:
    assert is_admin(12345, test_settings)
    assert not is_admin(99999, test_settings)


def test_require_admin_raises(test_settings: Settings) -> None:
    import pytest

    with pytest.raises(PermissionError):
        require_admin(99999, test_settings)


def test_redact_api_key() -> None:
    text = "api_key=supersecret123"
    result = redact_secrets(text)
    assert "supersecret" not in result
    assert "[REDACTED]" in result


def test_redact_bearer_token() -> None:
    text = "Authorization: Bearer abc.def.ghi"
    result = redact_secrets(text)
    assert "abc.def" not in result


def test_truncate_output() -> None:
    text = "x" * 1000
    result = truncate_output(text, 100)
    assert len(result.encode()) > 100
    assert "truncated" in result


def test_split_telegram_message() -> None:
    text = "a" * 5000
    chunks = split_telegram_message(text, max_len=4000)
    assert len(chunks) == 2
    assert all(len(c) <= 4000 for c in chunks)


def test_markdown_to_telegram_html_bold_and_italic() -> None:
    result = markdown_to_telegram_html("**bold** and *italic*")
    assert result == "<b>bold</b> and <i>italic</i>"


def test_markdown_to_telegram_html_code_and_link() -> None:
    result = markdown_to_telegram_html("Use `git status` and [docs](https://example.com)")
    assert "<code>git status</code>" in result
    assert '<a href="https://example.com">docs</a>' in result


def test_markdown_to_telegram_html_code_block() -> None:
    result = markdown_to_telegram_html("Before\n```python\nprint('hi')\n```\nAfter")
    assert "<pre><code>print('hi')</code></pre>" in result
    assert "Before" in result
    assert "After" in result


def test_sanitize_for_telegram_converts_markdown() -> None:
    result = sanitize_for_telegram("**done**", 1000)
    assert result == "<b>done</b>"
