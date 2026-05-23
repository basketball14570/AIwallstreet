"""Historical daily price provider for outcome backfill / labelling.

Fetches daily closes from Polygon aggregates; falls back to a deterministic
synthetic random walk (seeded per ticker) when no API key is set, so backfill
and retraining run end-to-end offline.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import httpx
import numpy as np
import pandas as pd

from app.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import TokenBucket
from tenacity import retry, stop_after_attempt, wait_exponential

log = get_logger("provider.prices")


class PriceHistoryProvider:
    def __init__(self, rate_per_min: float = 300):
        self._bucket = TokenBucket(rate=rate_per_min / 60.0, capacity=rate_per_min / 6.0)

    async def daily_closes(self, ticker: str, start: datetime, end: datetime) -> pd.Series:
        """Daily close series indexed by date (tz-naive), inclusive."""
        if not settings.polygon_api_key:
            return _synthetic_closes(ticker, start, end)
        return await self._fetch(ticker, start, end)

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, max=16))
    async def _fetch(self, ticker: str, start: datetime, end: datetime) -> pd.Series:
        await self._bucket.acquire()
        url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
               f"{start:%Y-%m-%d}/{end:%Y-%m-%d}")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, params={"adjusted": "true", "sort": "asc",
                                                  "limit": 50000,
                                                  "apiKey": settings.polygon_api_key})
            resp.raise_for_status()
            results = resp.json().get("results", [])
        if not results:
            return pd.Series(dtype=float)
        idx = [pd.Timestamp(r["t"], unit="ms").normalize() for r in results]
        return pd.Series([r["c"] for r in results], index=idx, name=ticker)

    async def current_price(self, ticker: str) -> float | None:
        """Latest price from the stock snapshot (15-min delayed on most plans).
        Falls back to the last synthetic close offline."""
        if not settings.polygon_api_key:
            end = datetime.now(tz=None)
            s = _synthetic_closes(ticker, end - timedelta(days=10), end)
            return round(float(s.iloc[-1]), 2) if len(s) else None
        try:
            await self._bucket.acquire()
            url = (f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/"
                   f"tickers/{ticker}")
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params={"apiKey": settings.polygon_api_key})
                resp.raise_for_status()
                t = resp.json().get("ticker", {})
            price = ((t.get("lastTrade") or {}).get("p")
                     or (t.get("day") or {}).get("c")
                     or (t.get("prevDay") or {}).get("c"))
            return round(float(price), 2) if price else None
        except Exception as exc:  # noqa: BLE001
            log.warning("current_price failed", ticker=ticker, error=str(exc))
            return None

    async def daily_bars(self, ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
        """Daily OHLC bars indexed by date (tz-naive). Columns: open/high/low/close.
        Falls back to a synthetic walk (with approximate intraday range) offline."""
        if not settings.polygon_api_key:
            closes = _synthetic_closes(ticker, start, end)
            rng = np.random.default_rng(abs(hash(ticker)) % (2**32) + 7)
            vol = rng.lognormal(mean=13.0, sigma=0.45, size=len(closes))
            return pd.DataFrame({
                "open": closes, "high": closes * 1.02,
                "low": closes * 0.98, "close": closes,
                "volume": vol,
            }, index=closes.index)
        return await self._fetch_bars(ticker, start, end)

    @retry(stop=stop_after_attempt(4), wait=wait_exponential(multiplier=2, max=16))
    async def _fetch_bars(self, ticker: str, start: datetime, end: datetime) -> pd.DataFrame:
        await self._bucket.acquire()
        url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
               f"{start:%Y-%m-%d}/{end:%Y-%m-%d}")
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, params={"adjusted": "true", "sort": "asc",
                                                  "limit": 50000,
                                                  "apiKey": settings.polygon_api_key})
            resp.raise_for_status()
            results = resp.json().get("results", [])
        if not results:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        idx = [pd.Timestamp(r["t"], unit="ms").normalize() for r in results]
        return pd.DataFrame(
            {"open": [r["o"] for r in results], "high": [r["h"] for r in results],
             "low": [r["l"] for r in results], "close": [r["c"] for r in results],
             "volume": [r.get("v", 0) for r in results]},
            index=idx,
        )


def _synthetic_closes(ticker: str, start: datetime, end: datetime) -> pd.Series:
    seed = abs(hash(ticker)) % (2**32)
    rng = np.random.default_rng(seed)
    days = pd.bdate_range(start.date(), end.date())
    if len(days) == 0:
        return pd.Series(dtype=float)
    # Heavy-tailed daily returns so some tickers genuinely "explode".
    rets = rng.standard_t(df=3, size=len(days)) * 0.03
    price = 20.0 * np.exp(np.cumsum(rets))
    return pd.Series(price, index=days, name=ticker)
