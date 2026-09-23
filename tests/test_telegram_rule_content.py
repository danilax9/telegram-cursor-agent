"""Dynamic telegram-bot.mdc rule content."""

from telegram_cursor_agent.agent.prompts import (
    TELEGRAM_RULE_LEGACY_FORMAT_SECTION,
    TELEGRAM_RULE_RICH_FORMAT_SECTION,
    build_telegram_rule_content,
)


def test_build_telegram_rule_content_preserves_frontmatter() -> None:
    content = build_telegram_rule_content(rich_messages=True)
    assert content.startswith("---\n")
    assert "alwaysApply: true" in content
    assert "## Identity" in content


def test_build_telegram_rule_content_rich_section() -> None:
    content = build_telegram_rule_content(rich_messages=True)
    assert "Rich Messages" in content
    assert TELEGRAM_RULE_RICH_FORMAT_SECTION.strip() in content
    assert "NEVER use triple backticks" not in content


def test_build_telegram_rule_content_legacy_section() -> None:
    content = build_telegram_rule_content(rich_messages=False)
    assert TELEGRAM_RULE_LEGACY_FORMAT_SECTION.strip() in content
    assert "NEVER use triple backticks" in content
