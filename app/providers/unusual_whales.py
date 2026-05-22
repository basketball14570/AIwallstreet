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


async def _synthetic_stream(interval: float) -> AsyncIterator[FlowEvent]:
    tickers = ["GME", "AMC", "SOFI", "PLTR", "RIVN", "MARA", "AAPL"]
    i = 0
    while True:
        spot = round(random.uniform(5, 60), 2)
        i += 1
        yield FlowEvent(
            source="unusual_whales",
            external_id=f"synthetic-{i}",
            ticker=random.choice(tickers),
            contract_type=ContractType.CALL,
            strike=round(spot * random.uniform(1.0, 1.2), 1),
            expiry=datetime.now(timezone.utc) + timedelta(days=random.choice([7, 14, 30])),
            side=random.choice([Side.ASK, Side.ASK, Side.MID, Side.BID]),
            is_sweep=random.random() > 0.4,
            is_spread=random.random() > 0.85,
            premium=round(random.uniform(50_000, 2_000_000), 0),
            size=random.randint(100, 5000),
            spot=spot,
            observed_at=datetime.now(timezone.utc),
        )
        await asyncio.sleep(interval)
