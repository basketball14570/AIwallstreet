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
        self._diagnosed = False               # one-time snapshot coverage report

    def _diagnose(self, underlying: str, contracts: list[dict]) -> None:
        """One-time report on what the live snapshot actually contains, so it's
        obvious from the logs whether the plan returns the fields the screener
        needs — especially last_quote/last_trade, without which the aggressor
        side (bought vs sold) can't be inferred and conviction stays flat."""
        self._diagnosed = True
        n = len(contracts)
        if not n:
            log.warning("polygon snapshot empty — can't assess field coverage; "
                        "retrying live during market hours is recommended",
                        ticker=underlying)
            return

        def pct(pred) -> int:
            return round(100 * sum(1 for c in contracts if pred(c)) / n)

        has_quote = pct(lambda c: bool(c.get("last_quote")))
        has_trade = pct(lambda c: bool(c.get("last_trade")))
        has_iv = pct(lambda c: c.get("implied_volatility") is not None)
        has_oi = pct(lambda c: bool(c.get("open_interest")))
        log.info("polygon snapshot field coverage", ticker=underlying, contracts=n,
                 last_quote_pct=has_quote, last_trade_pct=has_trade,
                 implied_volatility_pct=has_iv, open_interest_pct=has_oi)
        if has_oi == 0:
            log.warning("no open_interest in snapshot — vol/OI 'unusual' detection "
                        "will be meaningless; check your Polygon options entitlement")
        if has_iv == 0:
            log.warning("no implied_volatility in snapshot — IV-rank signal will stay "
                        "neutral; check your Polygon options entitlement")
        if has_quote == 0 and has_trade == 0:
            log.warning("no last_quote/last_trade in snapshot — aggressor side "
                        "(bought vs sold) can't be inferred, so conviction scores "
                        "will be flat. This is expected off-hours; re-check during "
                        "live market hours, else your tier may lack quotes/trades")

    async def _get(self, client: httpx.AsyncClient, url: str, params: dict) -> dict:
        await self._bucket.acquire()
        resp = await client.get(url, params=params, timeout=20)
        if resp.status_code in (401, 403):
            raise PermissionError(resp.text[:200])
        resp.raise_for_status()
        return resp.json()

    async def _snapshot(self, client: httpx.AsyncClient, underlying: str) -> list[dict]:
        # NB: the options-snapshot endpoint only supports sort=ticker (sort=volume
        # returns 400) and caps a page at 250. To surface the *most active*
        # contracts we page through the chain (following next_url) and keep the
        # highest day-volume contracts client-side.
        results: list[dict] = []
        url = SNAPSHOT_URL.format(underlying=underlying)
        params = {"apiKey": settings.polygon_api_key, "limit": 250}
        pages = max(1, settings.polygon_flow_max_pages)
        for _ in range(pages):
            body = await self._get(client, url, params)
            results.extend(body.get("results", []))
            next_url = body.get("next_url")
            if not next_url:
                break
            # next_url carries the cursor but not the key; resend only the key.
            url, params = next_url, {"apiKey": settings.polygon_api_key}
        results.sort(key=lambda c: (c.get("day") or {}).get("volume") or 0, reverse=True)
        top = settings.polygon_flow_top_contracts
        return results[:top] if top and top > 0 else results

    def _to_event(self, underlying: str, c: dict) -> FlowEvent | None:
        details = c.get("details", {})
        day = c.get("day", {})
        quote = c.get("last_quote", {})
        trade = c.get("last_trade", {})
        oi = c.get("open_interest") or 0
        volume = int(day.get("volume") or 0)
        if volume <= 0 or details.get("contract_type") not in ("call", "put"):
            return None

        # Unusual = today's volume swamps existing open interest. With zero OI
        # the ratio is undefined (treat as maximally unusual for the threshold),
        # but we store None — JSON/Postgres can't represent infinity.
        vol_oi = volume / oi if oi else float("inf")
        if vol_oi < settings.flow_min_vol_oi:
            return None
        stored_vol_oi = vol_oi if oi else None

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
            # Polygon snapshots don't flag sweeps. With a known side, treat a
            # large ask-side burst as a sweep; when side is unavailable (no
            # quotes/trades on the plan), a big single-poll volume burst stands
            # in as the urgency proxy.
            is_sweep=bool(delta >= 250 if side == Side.ASK
                          else (side is None and delta >= 500)),
            is_spread=False, premium=premium, size=delta, spot=spot,
            iv=float(iv) if iv is not None else None,
            open_interest=int(oi) if oi else None,
            vol_oi=stored_vol_oi,
            observed_at=datetime.now(timezone.utc), raw={"vol_oi": stored_vol_oi, "oi": oi},
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
                        contracts = await self._snapshot(client, underlying)
                        if not self._diagnosed:
                            self._diagnose(underlying, contracts)
                        for c in contracts:
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
