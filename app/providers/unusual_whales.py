"""Unusual Whales flow provider.

Polls the flow-alerts endpoint and normalises to FlowEvent. Falls back to a
synthetic generator when no API key is configured so the pipeline runs offline.
"""
from __future__ import annotations

import asyncio
import random
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import TokenBucket
from app.providers.base import FlowProvider
from app.schemas.flow import ContractType, FlowEvent, Side

log = get_logger("provider.uw")
BASE_URL = "https://api.unusualwhales.com/api"


class UnusualWhalesProvider(FlowProvider):
    name = "unusual_whales"

    def __init__(self, poll_interval: float = 5.0, rate_per_min: float = 120):
        self.poll_interval = poll_interval
        self._seen: set[str] = set()
        self._bucket = TokenBucket(rate=rate_per_min / 60.0, capacity=rate_per_min / 6.0)

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, max=16))
    async def _fetch(self, client: httpx.AsyncClient) -> list[dict]:
        await self._bucket.acquire()
        resp = await client.get(
            f"{BASE_URL}/option-trades/flow-alerts",
            headers={"Authorization": f"Bearer {settings.unusual_whales_api_key}"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("data", [])

    @staticmethod
    def normalise(row: dict) -> FlowEvent:
        return FlowEvent(
            source="unusual_whales",
            external_id=str(row.get("id")),
            ticker=row["ticker"],
            contract_type=ContractType(row.get("type", "call").lower()),
            strike=float(row["strike"]),
            expiry=datetime.fromisoformat(row["expiry"]),
            side=Side(row["side"]) if row.get("side") in {s.value for s in Side} else None,
            is_sweep=bool(row.get("is_sweep", False)),
            is_spread=bool(row.get("is_spread", False)),
            premium=float(row.get("total_premium", 0)),
            size=int(row.get("size", 0)),
            spot=float(row["underlying_price"]) if row.get("underlying_price") else None,
            observed_at=datetime.fromisoformat(row["executed_at"]),
            raw=row,
        )

    async def stream(self) -> AsyncIterator[FlowEvent]:
        if not settings.unusual_whales_api_key:
            log.warning("no UW API key — using synthetic flow")
            async for ev in _synthetic_stream(self.poll_interval):
                yield ev
            return
        async with httpx.AsyncClient() as client:
            while True:
                try:
                    for row in await self._fetch(client):
                        eid = str(row.get("id"))
                        if eid in self._seen:
                            continue
                        self._seen.add(eid)
                        yield self.normalise(row)
                except Exception as exc:  # noqa: BLE001
                    log.error("uw fetch failed", error=str(exc))
                await asyncio.sleep(self.poll_interval)


# Rough recent price levels so synthetic contracts look believable (spot near
# these, strikes a realistic distance OTM, expiries on real Friday opex dates).
_BASE_PRICES = {
    "GME": 28.0, "AMC": 4.2, "SOFI": 15.7, "PLTR": 140.0, "RIVN": 13.0,
    "MARA": 18.0, "AAPL": 230.0, "TSLA": 340.0, "NVDA": 140.0,
}


def _strike_increment(price: float) -> float:
    if price < 10:
        return 0.5
    if price < 25:
        return 1.0
    if price < 100:
        return 2.5
    if price < 250:
        return 5.0
    return 10.0


def _round_to(value: float, step: float) -> float:
    return round(round(value / step) * step, 2)


def _next_fridays(n: int = 6) -> list[datetime]:
    """Upcoming Friday expiries (weeklies/monthlies) from today."""
    today = datetime.now(timezone.utc)
    ahead = (4 - today.weekday()) % 7 or 7  # next Friday (not today)
    first = today + timedelta(days=ahead)
    return [(first + timedelta(weeks=w)).replace(hour=21, minute=0, second=0, microsecond=0)
            for w in range(n)]


def _new_contract() -> dict:
    ticker = random.choice(list(_BASE_PRICES))
    base = _BASE_PRICES[ticker]
    spot = round(base * random.uniform(0.97, 1.03), 2)        # small daily drift
    inc = _strike_increment(spot)
    strike = _round_to(spot * random.uniform(1.0, 1.12), inc)  # slightly OTM call
    return {"ticker": ticker, "spot": spot, "strike": strike,
            "expiry": random.choice(_next_fridays())}


async def _synthetic_stream(interval: float) -> AsyncIterator[FlowEvent]:
    """Emit realistic synthetic prints. ~60% of the time we add another print to
    an existing 'hot' contract (ask-side biased) so the per-contract roll-up
    shows volume accumulating, the way real repeated sweeps do; otherwise we
    spin up a new contract with a believable spot/strike/expiry."""
    hot: list[dict] = []   # recently-active contracts to pile more prints onto
    i = 0
    while True:
        i += 1
        if hot and random.random() < 0.6:
            c = random.choice(hot)
            side = random.choices([Side.ASK, Side.MID, Side.BID], weights=[7, 2, 1])[0]
            sweep = random.random() > 0.3
        else:
            c = _new_contract()
            hot.append(c)
            if len(hot) > 12:
                hot.pop(0)
            side = random.choices([Side.ASK, Side.MID, Side.BID], weights=[5, 2, 3])[0]
            sweep = random.random() > 0.4
        size = random.randint(100, 6000)
        # premium ≈ size × contract multiplier × a plausible per-contract price.
        unit_price = max(0.05, c["spot"] * random.uniform(0.01, 0.05))
        premium = round(size * 100 * unit_price, 0)
        yield FlowEvent(
            source="unusual_whales", external_id=f"synthetic-{i}",
            ticker=c["ticker"], contract_type=ContractType.CALL,
            strike=c["strike"], expiry=c["expiry"], side=side,
            is_sweep=sweep, is_spread=random.random() > 0.88,
            premium=premium, size=size, spot=c["spot"],
            observed_at=datetime.now(timezone.utc),
        )
        await asyncio.sleep(interval)
