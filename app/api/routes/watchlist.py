"""Watchlist CRUD."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.technicals import analyze
from app.db.base import get_session
from app.db.models import AnalystLevels, Watchlist
from app.providers.prices import PriceHistoryProvider

router = APIRouter(prefix="/watchlist", tags=["watchlist"])

_prices = PriceHistoryProvider()


class WatchlistIn(BaseModel):
    ticker: str
    note: str | None = None


@router.get("")
async def list_watchlist(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Watchlist))).scalars().all()
    return [{"ticker": w.ticker, "note": w.note} for w in rows]


def _level_entries(ta: dict, analyst: AnalystLevels | None,
                   current: float | None) -> list[dict]:
    raw: list[tuple[float, str]] = []
    if ta.get("data_source") != "unavailable":
        if ta.get("breakout_above"):
            raw.append((ta["breakout_above"], "chart resistance"))
        if ta.get("breakdown_below"):
            raw.append((ta["breakdown_below"], "chart support"))
    if analyst:
        raw += [(float(r), "analyst resistance") for r in (analyst.resistances or [])]
        raw += [(float(s), "analyst support") for s in (analyst.supports or [])]

    entries = []
    for value, label in raw:
        dist = round((value - current) / current * 100, 2) if current else None
        entries.append({"value": round(value, 2), "label": label,
                        "side": "above" if (current and value > current) else "below",
                        "dist_pct": dist})
    # Sort high → low; flag the nearest level above and below the current price.
    entries.sort(key=lambda e: e["value"], reverse=True)
    above = [e for e in entries if e["side"] == "above"]
    below = [e for e in entries if e["side"] == "below"]
    if above:
        min(above, key=lambda e: abs(e["dist_pct"]))["nearest"] = True
    if below:
        min(below, key=lambda e: abs(e["dist_pct"]))["nearest"] = True
    return entries


@router.get("/overview")
async def overview(session: AsyncSession = Depends(get_session)):
    """Each watchlist ticker with its current price and every monitored level
    (chart + analyst) plus distance %, for an at-a-glance view."""
    wl = (await session.execute(select(Watchlist))).scalars().all()
    notes = {w.ticker: w.note for w in wl}
    analyst = {a.ticker: a for a in
               (await session.execute(select(AnalystLevels))).scalars().all()}
    tickers = list(notes.keys())

    async def _fetch(t: str):
        cur, ta = await asyncio.gather(_prices.current_price(t), analyze(t))
        return t, cur, ta

    results = await asyncio.gather(*(_fetch(t) for t in tickers))
    out = []
    for t, cur, ta in results:
        out.append({
            "ticker": t, "note": notes.get(t), "current": cur,
            "prev_close": None if ta.get("data_source") == "unavailable" else ta.get("spot"),
            "levels": _level_entries(ta, analyst.get(t), cur),
        })
    out.sort(key=lambda r: r["ticker"])
    return out


@router.post("", status_code=201)
async def add_watchlist(body: WatchlistIn, session: AsyncSession = Depends(get_session)):
    w = Watchlist(ticker=body.ticker.upper(), note=body.note)
    session.add(w)
    try:
        await session.commit()
    except Exception:  # noqa: BLE001
        await session.rollback()
        raise HTTPException(409, "ticker already on watchlist")
    return {"ticker": w.ticker}


@router.delete("/{ticker}", status_code=204)
async def remove_watchlist(ticker: str, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(Watchlist).where(Watchlist.ticker == ticker.upper()))
    await session.commit()
