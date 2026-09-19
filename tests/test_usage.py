"""Cursor usage service tests."""

from datetime import UTC, datetime

from telegram_cursor_agent.services.usage import (
    CursorUsageService,
    format_usage_message,
)


def test_format_usage_message(test_settings) -> None:
    from telegram_cursor_agent.services.usage import CursorUsageSnapshot, UsagePool

    snapshot = CursorUsageSnapshot(
        plan_name="Pro",
        plan_price="$20/mo",
        billing_cycle_start=datetime(2026, 8, 19, tzinfo=UTC),
        billing_cycle_end=datetime(2026, 9, 19, tzinfo=UTC),
        cursor_models=UsagePool("Cursor Models (Composer, Grok)", 90.7),
        other_models=UsagePool("Other Models (API)", 20.0),
        total_percent_used=84.3,
        included_amount_usd=20.0,
        display_message=None,
    )
    text = format_usage_message(snapshot)
    assert "Pro" in text
    assert "90%" in text or "90.7%" in text
    assert "20%" in text
    assert "Composer, Grok" in text


def test_parse_snapshot(test_settings) -> None:
    service = CursorUsageService(test_settings)
    snapshot = service._parse_snapshot(
        {
            "billingCycleStart": "1787320552000",
            "billingCycleEnd": "1789998952000",
            "planUsage": {
                "autoPercentUsed": 90.74,
                "apiPercentUsed": 19.98,
                "totalPercentUsed": 84.31,
            },
        },
        {"planInfo": {"planName": "Pro", "price": "$20/mo", "includedAmountCents": 2000}},
    )
    assert snapshot.plan_name == "Pro"
    assert round(snapshot.cursor_models.percent_used, 2) == 90.74
    assert round(snapshot.other_models.percent_used, 2) == 19.98
    assert snapshot.included_amount_usd == 20.0
