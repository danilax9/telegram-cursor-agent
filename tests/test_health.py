"""Health signal names shared with scripts/tca_guard.py."""

from telegram_cursor_agent.services.health import (
    errors_key,
    heartbeat_key,
    is_transient_delivery_error,
)


def test_keys_match_guard_contract() -> None:
    assert heartbeat_key("worker") == "tca:health:worker"
    assert errors_key("bot") == "tca:health:bot:errors"


def test_network_errors_are_transient() -> None:
    assert is_transient_delivery_error(TimeoutError())
    assert is_transient_delivery_error(RuntimeError("bug")) is False
