"""Market-regime provider — samples macro inputs and classifies the tape.

Regime changes slowly relative to flow, so it is cached in Redis with a short
TTL and shared across all scoring consumers. Mock fallback (random-but-coherent
macro snapshot) when no Polygon key is set so the pipeline runs offline.
"""
from __future__ import annotations

import json
import random

import httpx

from app.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import TokenBucket
from app.core.redis_client import get_redis
from app.scoring.regime import MacroContext, Regime, detect_regime

log = get_logger("provider.regime")
CACHE_KEY = "regime:current"
CACHE_TTL = 60  # seconds


class RegimeProvider:
    def __init__(self, rate_per_min: float = 60):
        self._bucket = TokenBucket(rate=rate_per_min / 60.0, capacity=rate_per_min / 6.0)

    async def get_regime(self) -> Regime:
        cached = await get_redis().get(CACHE_KEY)
        if cached:
            d = json.loads(cached)
            return Regime(d["name"], d["bullish_multiplier"], d["confidence_scale"],
                          d.get("reason", ""))
        macro = await self._sample()
        regime = detect_regime(macro)
        await get_redis().setex(CACHE_KEY, CACHE_TTL, json.dumps({
            "name": regime.name, "bullish_multiplier": regime.bullish_multiplier,
            "confidence_scale": regime.confidence_scale, "reason": regime.reason,
        }))
        return regime

    async def _sample(self) -> MacroContext:
        if not settings.polygon_api_key:
            return _mock_macro()
        return await self._fetch()

    async def _fetch(self) -> MacroContext:
        try:
            await self._bucket.acquire()
            async with httpx.AsyncClient(timeout=15) as client:
                # VIX via index ticker; breadth/skew/gamma require additional
                # vendor feeds — wire them here. Hooks left explicit.
                vix_resp = await client.get(
                    "https://api.polygon.io/v2/snapshot/locale/global/markets/indices/tickers/I:VIX",
                    params={"apiKey": settings.polygon_api_key},
                )
                vix_resp.raise_for_status()
                vix = vix_resp.json().get("results", {}).get("value")
                return MacroContext(vix=vix)
        except Exception as exc:  # noqa: BLE001
            log.error("regime fetch failed", error=str(exc))
            return MacroContext()


def _mock_macro() -> MacroContext:
    vix = round(random.uniform(11, 35), 1)
    return MacroContext(
        vix=vix,
        vix_trend=round(random.uniform(-0.2, 0.3), 2),
        spy_realized_vol=round(random.uniform(0.08, 0.4), 2),
        breadth=round(random.uniform(0.2, 0.8), 2),
        putcall_skew=round(random.uniform(0.7, 1.4), 2),
        smallcap_rs=round(random.uniform(0.95, 1.08), 3),
        aggregate_gamma=round(random.uniform(-1, 1), 2),
    )
