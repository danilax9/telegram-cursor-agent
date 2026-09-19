"""Cursor plan usage and limits via the dashboard API."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx

from telegram_cursor_agent.core.config import Settings

CURSOR_API_BASE = "https://api2.cursor.sh"
USAGE_PATH = "/aiserver.v1.DashboardService/GetCurrentPeriodUsage"
PLAN_INFO_PATH = "/aiserver.v1.DashboardService/GetPlanInfo"


@dataclass
class UsagePool:
    label: str
    percent_used: float


@dataclass
class CursorUsageSnapshot:
    plan_name: str
    plan_price: str
    billing_cycle_start: datetime
    billing_cycle_end: datetime
    cursor_models: UsagePool
    other_models: UsagePool
    total_percent_used: float
    included_amount_usd: float | None
    display_message: str | None


class CursorUsageError(Exception):
    pass


class CursorUsageService:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    async def fetch_usage(self) -> CursorUsageSnapshot:
        token = self._load_access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Connect-Protocol-Version": "1",
        }
        async with httpx.AsyncClient(base_url=CURSOR_API_BASE, timeout=20.0) as client:
            usage_response = await client.post(USAGE_PATH, headers=headers, json={})
            plan_response = await client.post(PLAN_INFO_PATH, headers=headers, json={})

        if usage_response.status_code in {401, 403}:
            raise CursorUsageError("Cursor session expired. Run `agent login` on the server.")
        usage_response.raise_for_status()
        plan_response.raise_for_status()

        usage_payload = usage_response.json()
        plan_payload = plan_response.json()
        return self._parse_snapshot(usage_payload, plan_payload)

    def _load_access_token(self) -> str:
        for path in self._auth_file_candidates():
            if not path.is_file():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            token = data.get("accessToken") or data.get("access_token")
            if isinstance(token, str) and token.strip():
                return token.strip()
        searched = ", ".join(str(path) for path in self._auth_file_candidates())
        raise CursorUsageError(f"Cursor auth token not found. Checked: {searched}")

    def _auth_file_candidates(self) -> list[Path]:
        home = Path.home()
        candidates = [
            self._settings.cursor_auth_file,
            home / ".config" / "cursor" / "auth.json",
            home / ".config" / "Cursor" / "auth.json",
        ]
        unique: list[Path] = []
        for path in candidates:
            if path not in unique:
                unique.append(path)
        return unique

    @staticmethod
    def _parse_snapshot(usage_payload: object, plan_payload: object) -> CursorUsageSnapshot:
        if not isinstance(usage_payload, dict) or not isinstance(plan_payload, dict):
            raise CursorUsageError("Unexpected Cursor usage response.")

        plan_usage = usage_payload.get("planUsage")
        if not isinstance(plan_usage, dict):
            raise CursorUsageError("Cursor usage data is unavailable for this account.")

        plan_info = plan_payload.get("planInfo")
        if not isinstance(plan_info, dict):
            plan_info = {}

        billing_start = _parse_epoch_ms(usage_payload.get("billingCycleStart"))
        billing_end = _parse_epoch_ms(usage_payload.get("billingCycleEnd"))
        included_cents = plan_info.get("includedAmountCents")
        included_amount_usd = (
            float(included_cents) / 100 if isinstance(included_cents, (int, float)) else None
        )

        return CursorUsageSnapshot(
            plan_name=str(plan_info.get("planName", "Unknown")),
            plan_price=str(plan_info.get("price", "")),
            billing_cycle_start=billing_start,
            billing_cycle_end=billing_end,
            cursor_models=UsagePool(
                label="Cursor Models (Composer, Grok)",
                percent_used=float(plan_usage.get("autoPercentUsed", 0.0)),
            ),
            other_models=UsagePool(
                label="Other Models (API)",
                percent_used=float(plan_usage.get("apiPercentUsed", 0.0)),
            ),
            total_percent_used=float(plan_usage.get("totalPercentUsed", 0.0)),
            included_amount_usd=included_amount_usd,
            display_message=(
                str(usage_payload["displayMessage"])
                if usage_payload.get("displayMessage")
                else None
            ),
        )


def format_usage_message(snapshot: CursorUsageSnapshot) -> str:
    period = (
        f"{snapshot.billing_cycle_start.strftime('%d.%m.%Y')} — "
        f"{snapshot.billing_cycle_end.strftime('%d.%m.%Y')}"
    )
    lines = [
        f"**Лимиты Cursor — {snapshot.plan_name}**",
        "",
        f"Период: {period}",
    ]
    if snapshot.plan_price:
        lines.append(f"Тариф: {snapshot.plan_price}")
    if snapshot.included_amount_usd is not None:
        lines.append(f"Included usage: ${snapshot.included_amount_usd:.2f}")

    lines.extend(
        [
            "",
            f"**{snapshot.cursor_models.label}**",
            f"{_format_percent(snapshot.cursor_models.percent_used)}",
            "",
            f"**{snapshot.other_models.label}**",
            f"{_format_percent(snapshot.other_models.percent_used)}",
            "",
            "**Общий included usage**",
            f"{_format_percent(snapshot.total_percent_used)}",
        ]
    )

    if snapshot.display_message:
        lines.extend(["", f"_{snapshot.display_message}_"])

    lines.extend(["", "[Spending dashboard](https://cursor.com/dashboard/spending)"])
    return "\n".join(lines)


def _format_percent(value: float) -> str:
    rounded = round(value, 1)
    display = int(rounded) if rounded.is_integer() else rounded
    bar = _progress_bar(value)
    return f"{display}% {bar}"


def _progress_bar(value: float, width: int = 10) -> str:
    clamped = max(0.0, min(value, 100.0))
    filled = round(clamped / 100 * width)
    return "█" * filled + "░" * (width - filled)


def _parse_epoch_ms(value: object) -> datetime:
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value) / 1000, tz=UTC)
    return datetime.now(tz=UTC)
