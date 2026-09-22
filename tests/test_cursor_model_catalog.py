"""Cursor model catalog grouping tests."""

from telegram_cursor_agent.services.cursor_model_catalog import (
    build_model_catalog,
    detect_family,
    find_base,
    find_family,
    split_base_and_tier,
)

SAMPLE_MODELS = [
    {"id": "auto", "label": "Auto (default)"},
    {"id": "composer-2.5", "label": "Composer 2.5 (current)"},
    {"id": "composer-2.5-fast", "label": "Composer 2.5 Fast"},
    {"id": "gpt-5.3-codex-low-fast", "label": "Codex 5.3 Low Fast"},
    {"id": "gpt-5.3-codex-high", "label": "Codex 5.3 High"},
    {"id": "gpt-5.2", "label": "GPT-5.2"},
    {"id": "claude-opus-5-thinking-high-fast", "label": "Claude Opus 5 1M Thinking Fast"},
    {"id": "claude-sonnet-5-high", "label": "Claude Sonnet 5 1M"},
    {"id": "cursor-grok-4.6-high-fast", "label": "Cursor Grok 4.6 Fast"},
]


def test_split_base_and_tier() -> None:
    assert split_base_and_tier("gpt-5.3-codex-low-fast", "Codex 5.3 Low Fast") == (
        "gpt-5.3-codex",
        "Low Fast",
    )
    assert split_base_and_tier(
        "claude-opus-5-thinking-high-fast",
        "Claude Opus 5 1M Thinking Fast",
    ) == (
        "claude-opus-5-thinking",
        "Fast",
    )
    assert split_base_and_tier("composer-2.5", "Composer 2.5 (current)") == (
        "composer-2.5",
        "",
    )


def test_detect_family() -> None:
    assert detect_family("composer-2.5", "Composer 2.5 (current)") == (
        "cursor",
        "Cursor",
    )
    assert detect_family("auto", "Auto (default)") == ("cursor", "Cursor")
    assert detect_family("gpt-5.3-codex-high", "Codex 5.3 High") == (
        "gpt",
        "ChatGPT",
    )
    assert detect_family("gpt-5.2", "GPT-5.2") == ("gpt", "ChatGPT")
    assert detect_family("claude-opus-5-high", "Claude Opus 5 1M") == (
        "claude",
        "Claude",
    )
    assert detect_family("claude-sonnet-5-high", "Claude Sonnet 5 1M") == (
        "claude",
        "Claude",
    )
    assert detect_family("cursor-grok-4.6-high-fast", "Cursor Grok 4.6 Fast") == (
        "cursor",
        "Cursor",
    )


def test_build_model_catalog_groups_variants() -> None:
    catalog = build_model_catalog(SAMPLE_MODELS)

    cursor = find_family(catalog, "cursor")
    assert cursor is not None
    assert len(cursor.bases) == 3
    composer = find_base(cursor, "composer-2.5")
    assert composer is not None
    assert len(composer.variants) == 2

    gpt = find_family(catalog, "gpt")
    assert gpt is not None
    codex = find_base(gpt, "gpt-5.3-codex")
    assert codex is not None
    assert {variant.tier_label for variant in codex.variants} == {
        "Low Fast",
        "High",
    }
    assert find_base(gpt, "gpt-5.2") is not None

    claude = find_family(catalog, "claude")
    assert claude is not None
    assert len(claude.bases) == 2
    thinking = find_base(claude, "claude-opus-5-thinking")
    assert thinking is not None
    assert thinking.variants[0].tier_label == "Fast"
