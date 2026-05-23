"""Optional upcoming-earnings lookup (Finnhub free tier).

Polygon's Stocks Starter plan doesn't include earnings dates, so this is an
opt-in extra: set FINNHUB_API_KEY and cards gain a 'reports in N days' warning.
With no key it returns None and the rest of the system simply omits earnings —
we never fabricate a date.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

import httpx

from app.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis

log = get_logger("provider.earnings")
CACHE_TTL = 6 * 3600  # earnings dates move rarely; cache for 6h


class EarningsProvider:
    async def days_to_earnings(self, ticker: str) -> float | None:
        """Calendar days until the next earnings report, or None if unknown."""
        if not settings.finnhub_api_key:
            return None
        cache_key = f"earn:{ticker}"
        cached = await get_redis().get(cache_key)
        if cached is not None:
            val = json.loads(cached)
            return None if val is None else float(val)

        days = await self._fetch(ticker)
        await get_redis().setex(cache_key, CACHE_TTL, json.dumps(days))
        return days

    async def _fetch(self, ticker: str) -> float | None:
        today = date.today()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://finnhub.io/api/v1/calendar/earnings",
                    params={"symbol": ticker, "from": today.isoformat(),
                            "to": (today + timedelta(days=120)).isoformat(),
                            "token": settings.finnhub_api_key},
                )
                resp.raise_for_status()
                cal = resp.json().get("earningsCalendar", [])
        except Exception as exc:  # noqa: BLE001
            log.warning("earnings fetch failed", ticker=ticker, error=str(exc))
            return None
        upcoming = sorted(
            d for d in (_parse(e.get("date")) for e in cal) if d and d >= today
        )
        return float((upcoming[0] - today).days) if upcoming else None


def _parse(s: str | None) -> date | None:
    try:
        return date.fromisoformat(s) if s else None
    except ValueError:
        return None
