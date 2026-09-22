"""Cursor account registry tests."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

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


def test_main_account_restored_after_backup_switch(
    accounts_file, test_settings, tmp_path: Path
) -> None:
    live = test_settings.cursor_auth_file
    live.write_text('{"accessToken":"token-a"}', encoding="utf-8")
    registry = json.loads(accounts_file.read_text(encoding="utf-8"))
    registry["accounts"][0]["auth_file"] = str(live)
    accounts_file.write_text(json.dumps(registry), encoding="utf-8")

    service = CursorAccountService(test_settings)
    service.list_accounts()
    service.activate_account_files(service.get_account("backup"))
    assert live.read_text(encoding="utf-8") == '{"accessToken":"token-b"}'

    service.activate_account_files(service.get_account("main"))
    assert live.read_text(encoding="utf-8") == '{"accessToken":"token-a"}'
    isolated = test_settings.cursor_accounts_dir / "main" / ".config" / "cursor" / "auth.json"
    assert isolated.is_file()


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


def test_can_activate_accounts_locally_writable(tmp_path, test_settings) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    test_settings.cursor_auth_file = auth
    service = CursorAccountService(test_settings)
    assert service.can_activate_accounts_locally() is True


@pytest.mark.asyncio
async def test_get_active_account_reads_redis(
    accounts_file, test_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    redis = AsyncMock()
    redis.get = AsyncMock(return_value=b"backup")

    service = CursorAccountService(test_settings, redis)
    active = await service.get_active_account()
    assert active.id == "backup"


def test_should_switch_on_worker_when_self_deploy(
    accounts_file, test_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    test_settings.self_deploy_enabled = True
    service = CursorAccountService(test_settings, None)
    assert service.should_switch_on_worker() is True


def test_can_activate_accounts_locally_read_only(
    tmp_path, test_settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    auth = tmp_path / "auth.json"
    auth.write_text("{}", encoding="utf-8")
    test_settings.cursor_auth_file = auth
    real_access = __import__("os").access

    def _access(path, mode):  # type: ignore[no-untyped-def]
        if path == auth:
            return False
        return real_access(path, mode)

    monkeypatch.setattr(
        "telegram_cursor_agent.services.cursor_accounts.os.access",
        _access,
    )
    service = CursorAccountService(test_settings)
    assert service.can_activate_accounts_locally() is False


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
