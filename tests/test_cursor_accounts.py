"""Cursor account registry tests."""

import json
from pathlib import Path

import pytest

from telegram_cursor_agent.services.cursor_accounts import CursorAccountService
from telegram_cursor_agent.services.usage import CursorUsageSnapshot, UsagePool


@pytest.fixture
def accounts_file(tmp_path: Path, test_settings, monkeypatch: pytest.MonkeyPatch):
    auth_a = tmp_path / "a.json"
    auth_b = tmp_path / "b.json"
    auth_a.write_text('{"accessToken":"token-a"}', encoding="utf-8")
    auth_b.write_text('{"accessToken":"token-b"}', encoding="utf-8")
    registry = tmp_path / "accounts.json"
    registry.write_text(
        json.dumps(
            {
                "auto_rotate": True,
                "usage_threshold_percent": 95,
                "accounts": [
                    {
                        "id": "main",
                        "label": "Main",
                        "auth_file": str(auth_a),
                        "priority": 0,
                    },
                    {
                        "id": "backup",
                        "label": "Backup",
                        "auth_file": str(auth_b),
                        "priority": 1,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    test_settings.cursor_accounts_file = registry
    test_settings.cursor_accounts_dir = tmp_path / "homes"
    test_settings.cursor_auth_file = tmp_path / "active-auth.json"
    return registry


def test_load_accounts(accounts_file, test_settings) -> None:
    service = CursorAccountService(test_settings)
    accounts = service.list_accounts()
    assert [account.id for account in accounts] == ["main", "backup"]


def test_activate_account_copies_auth(accounts_file, test_settings) -> None:
    service = CursorAccountService(test_settings)
    account = service.get_account("backup")
    service.activate_account_files(account)
    assert test_settings.cursor_auth_file.read_text(encoding="utf-8") == (
        '{"accessToken":"token-b"}'
    )


def test_activate_same_auth_file_is_noop(accounts_file, test_settings) -> None:
    service = CursorAccountService(test_settings)
    account = service.get_account("main")
    test_settings.cursor_auth_file = account.auth_file
    service.activate_account_files(account)


def test_activate_account_accepts_auth_file_that_is_already_active(
    accounts_file, test_settings
) -> None:
    service = CursorAccountService(test_settings)
    account = service.get_account("main")
    test_settings.cursor_auth_file = account.auth_file

    service.activate_account_files(account)

    assert account.auth_file.read_text(encoding="utf-8") == '{"accessToken":"token-a"}'


def test_account_env_does_not_treat_login_token_as_api_key(
    accounts_file, test_settings
) -> None:
    service = CursorAccountService(test_settings)
    account = service.get_account("main")
    test_settings.cursor_auth_file = account.auth_file

    assert service.account_env(account) == {}


def test_register_account_writes_registry(accounts_file, test_settings, tmp_path: Path) -> None:
    service = CursorAccountService(test_settings)
    auth_file = tmp_path / "new-auth.json"
    auth_file.write_text('{"accessToken":"token-new"}', encoding="utf-8")
    account = service.register_account("newacc", auth_file, label="New")
    assert account.id == "newacc"
    reloaded = CursorAccountService(test_settings).get_account("newacc")
    assert reloaded.auth_file == auth_file


def test_is_exhausted(test_settings) -> None:
    service = CursorAccountService(test_settings)
    snapshot = CursorUsageSnapshot(
        plan_name="Pro",
        plan_price="$20",
        billing_cycle_start=__import__("datetime").datetime.now(__import__("datetime").UTC),
        billing_cycle_end=__import__("datetime").datetime.now(__import__("datetime").UTC),
        cursor_models=UsagePool("Cursor Models", 96.0),
        other_models=UsagePool("Other", 10.0),
        total_percent_used=96.0,
        included_amount_usd=20.0,
        display_message=None,
    )
    assert service.is_exhausted(snapshot, 95.0) is True
