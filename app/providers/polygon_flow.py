"""Polygon-derived unusual options flow.

Unusual Whales ships a *curated* flow feed; Polygon does not — so we build our
own. For each watched underlying we poll Polygon's option-chain snapshot, look
for contracts whose **day volume is large relative to open interest** (the
classic "unusual" tell), infer the aggressor side from the last trade vs the
quote, and emit a normalised ``FlowEvent`` per fresh burst of volume.

Requires a Polygon plan with options data (the free tier is stocks-only); on a
``NOT_AUTHORIZED`` response — or when no key is set — we log why and fall back
to the synthetic generator so the pipeline still runs.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.core.logging import get_logger
from app.core.ratelimit import TokenBucket
from app.providers.base import FlowProvider
from app.providers.unusual_whales import _synthetic_stream
from app.schemas.flow import ContractType, FlowEvent, Side

log = get_logger("provider.polygon_flow")
SNAPSHOT_URL = "https://api.polygon.io/v3/snapshot/options/{underlying}"


def _infer_side(last_price: float | None, bid: float | None, ask: float | None) -> Side | None:
    if last_price is None or bid is None or ask is None or ask <= bid:
        return None
    span = ask - bid
    if last_price >= ask - 0.2 * span:
        return Side.ASK          # lifting the offer — aggressive buyer
    if last_price <= bid + 0.2 * span:
        return Side.BID          # hitting the bid — aggressive seller
    return Side.MID


class PolygonFlowProvider(FlowProvider):
    name = "polygon_flow"

    def __init__(self, poll_interval: float | None = None, rate_per_min: float = 90):
        self.poll_interval = poll_interval or settings.polygon_flow_poll_sec
        self.tickers = [t.strip().upper() for t in settings.watch_tickers.split(",") if t.strip()]
        self._bucket = TokenBucket(rate=rate_per_min / 60.0, capacity=rate_per_min / 6.0)
        self._seen_vol: dict[str, int] = {}   # option ticker -> last day volume emitted

    async def _snapshot(self, client: httpx.AsyncClient, underlying: str) -> list[dict]:
        await self._bucket.acquire()
        resp = await client.get(
            SNAPSHOT_URL.format(underlying=underlying),
            params={"apiKey": settings.polygon_api_key, "limit": 250,
                    "order": "desc", "sort": "volume"},
            timeout=20,
        )
        if resp.status_code in (401, 403):
            raise PermissionError(resp.text[:200])
        resp.raise_for_status()
        return resp.json().get("results", [])

    def _to_event(self, underlying: str, c: dict) -> FlowEvent | None:
        details = c.get("details", {})
        day = c.get("day", {})
        quote = c.get("last_quote", {})
        trade = c.get("last_trade", {})
        oi = c.get("open_interest") or 0
        volume = int(day.get("volume") or 0)
        if volume <= 0 or details.get("contract_type") not in ("call", "put"):
            return None

        # Unusual = today's volume swamps existing open interest.
        vol_oi = volume / oi if oi else float("inf")
        if vol_oi < settings.flow_min_vol_oi:
            return None

        opt_ticker = details.get("ticker") or c.get("ticker", "")
        prev = self._seen_vol.get(opt_ticker, 0)
        delta = volume - prev
        if delta <= 0:                      # nothing new since last poll
            return None
        self._seen_vol[opt_ticker] = volume

        price = trade.get("price") or day.get("close") or day.get("vwap") or 0.0
        premium = round(delta * 100 * price, 0)
        if premium < settings.flow_min_premium:
            return None

        spot = (c.get("underlying_asset", {}) or {}).get("price")
        side = _infer_side(trade.get("price"), quote.get("bid"), quote.get("ask"))
        expiry = datetime.fromisoformat(details["expiration_date"]).replace(tzinfo=timezone.utc)
        iv = c.get("implied_volatility")
        return FlowEvent(
            source="polygon", external_id=f"{opt_ticker}:{volume}",
            ticker=underlying, contract_type=ContractType(details["contract_type"]),
            strike=float(details["strike_price"]), expiry=expiry, side=side,
            # Polygon snapshots don't flag sweeps; approximate an aggressive
            # buy as a large ask-side burst.
            is_sweep=bool(side == Side.ASK and delta >= 250),
            is_spread=False, premium=premium, size=delta, spot=spot,
            iv=float(iv) if iv is not None else None,
            open_interest=int(oi) if oi else None,
            vol_oi=vol_oi if oi else None,
            observed_at=datetime.now(timezone.utc), raw={"vol_oi": vol_oi, "oi": oi},
        )

    async def stream(self) -> AsyncIterator[FlowEvent]:
        if not settings.polygon_api_key:
            log.warning("no Polygon API key — using synthetic flow")
            async for ev in _synthetic_stream(5.0):
                yield ev
            return
        log.info("polygon flow starting", tickers=self.tickers)
        async with httpx.AsyncClient() as client:
            while True:
                for underlying in self.tickers:
                    try:
                        for c in await self._snapshot(client, underlying):
                            ev = self._to_event(underlying, c)
                            if ev is not None:
                                yield ev
                    except PermissionError as exc:
                        log.error("polygon options not authorized — your plan likely "
                                  "lacks options data; falling back to synthetic",
                                  error=str(exc))
                        async for ev in _synthetic_stream(5.0):
                            yield ev
                        return
                    except Exception as exc:  # noqa: BLE001
                        log.error("polygon flow fetch failed", ticker=underlying, error=str(exc))
                await asyncio.sleep(self.poll_interval)
