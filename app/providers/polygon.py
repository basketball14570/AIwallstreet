"""Polygon.io + reference-data context provider.

Aggregates float, short interest, relative volume, open-interest change and
gamma exposure into a MarketContext. Cached in Redis to avoid hammering APIs.
Returns mock context when no API key is configured.
"""
from __future__ import annotations

import json
import random

import httpx

from app.config import settings
from app.core.logging import get_logger
from app.core.redis_client import get_redis
from app.providers.base import ContextProvider
from app.providers.sentiment import SentimentProvider
from app.schemas.flow import MarketContext

log = get_logger("provider.polygon")
CACHE_TTL = 300  # seconds


class PolygonContextProvider(ContextProvider):
    def __init__(self, sentiment: SentimentProvider | None = None):
        self.sentiment = sentiment or SentimentProvider()

    async def get_context(self, ticker: str) -> MarketContext:
        cache_key = f"ctx:{ticker}"
        cached = await get_redis().get(cache_key)
        if cached:
            return MarketContext(**json.loads(cached))

        ctx = (
            await self._fetch(ticker)
            if settings.polygon_api_key
            else _mock_context(ticker)
        )
        sent = await self.sentiment.score(ticker)
        ctx.social_score = sent["social"]
        ctx.news_score = sent["news"]
        await get_redis().setex(cache_key, CACHE_TTL, ctx.model_dump_json())
        return ctx

    async def _fetch(self, ticker: str) -> MarketContext:
        async with httpx.AsyncClient(timeout=15) as client:
            try:
                # Snapshot for relative volume.
                snap = await client.get(
                    f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}",
                    params={"apiKey": settings.polygon_api_key},
                )
                snap.raise_for_status()
                day = snap.json().get("ticker", {}).get("day", {})
                # Short interest / float require a fundamentals vendor; left as
                # hooks. Replace with your data source.
                return MarketContext(
                    ticker=ticker,
                    stock_rvol=_safe_ratio(day.get("v"), day.get("av")),
                    rel_options_volume=None,
                )
            except Exception as exc:  # noqa: BLE001
                log.error("polygon fetch failed", ticker=ticker, error=str(exc))
                return MarketContext(ticker=ticker)


def _safe_ratio(num, den):
    try:
        return float(num) / float(den) if num and den else None
    except (TypeError, ZeroDivisionError):
        return None


def _mock_context(ticker: str) -> MarketContext:
    return MarketContext(
        ticker=ticker,
        float_shares=random.choice([8e6, 15e6, 40e6, 120e6]),
        short_interest_pct=round(random.uniform(2, 35), 1),
        borrow_rate=round(random.uniform(1, 90), 1),
        rel_options_volume=round(random.uniform(1, 12), 1),
        stock_rvol=round(random.uniform(0.8, 6), 1),
        oi_change_ratio=round(random.uniform(0, 2), 2),
        dealer_gamma=round(random.uniform(-1, 1), 2),
        historical_similarity=round(random.uniform(0, 1), 2),
    )
