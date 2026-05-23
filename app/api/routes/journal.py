"""Trade-idea journal.

Save an idea from an alert (one tap on the link in the notification), then a
background job fills forward prices so you can see whether the move actually
played out — turning the 'this is only a hint' framing into a real track record.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import JournalEntry
from app.providers.prices import PriceHistoryProvider

router = APIRouter(prefix="/journal", tags=["journal"])

_prices = PriceHistoryProvider()


class JournalIn(BaseModel):
    ticker: str
    type: str = "call"
    strike: float | None = None
    expiry: str | None = None
    lean: str | None = None
    entry_price: float | None = None
    note: str | None = None
    source: str = "manual"


async def _create(data: JournalIn, session: AsyncSession) -> JournalEntry:
    lean = (data.lean or ("bullish" if data.type.lower() == "call" else "bearish")).lower()
    entry_price = data.entry_price
    if entry_price is None:
        entry_price = await _prices.current_price(data.ticker.upper())
    expiry = None
    if data.expiry:
        try:
            expiry = datetime.fromisoformat(data.expiry)
        except ValueError:
            expiry = None
    entry = JournalEntry(
        ticker=data.ticker.upper(), contract_type=data.type.lower(),
        strike=data.strike, expiry=expiry, lean=lean,
        entry_price=entry_price, note=data.note, source=data.source,
    )
    session.add(entry)
    await session.commit()
    await session.refresh(entry)
    return entry


def _serialize(e: JournalEntry) -> dict:
    sign = 1.0 if e.lean == "bullish" else -1.0

    def move(p: float | None) -> dict | None:
        if p is None or not e.entry_price:
            return None
        pct = (p - e.entry_price) / e.entry_price * 100.0
        return {"price": p, "pct_change": round(pct, 2),
                "favorable": (pct * sign) > 0}

    return {
        "id": e.id, "ticker": e.ticker, "contract_type": e.contract_type,
        "strike": e.strike,
        "expiry": e.expiry.date().isoformat() if e.expiry else None,
        "lean": e.lean, "entry_price": e.entry_price,
        "created_at": e.created_at.isoformat() if e.created_at else None,
        "source": e.source, "note": e.note,
        "intraday": move(e.intraday_price),
        "next_open": move(e.next_open_price),
        "next_close": move(e.next_close_price),
        "day3": move(e.day3_price),
    }


@router.get("/add", response_class=HTMLResponse)
async def add_via_link(
    ticker: str = Query(...),
    type: str = "call",
    strike: float | None = None,
    expiry: str | None = None,
    lean: str | None = None,
    entry_price: float | None = None,
    source: str = "alert",
    session: AsyncSession = Depends(get_session),
):
    """Clickable target for the 'Add to journal' link in alerts. Returns a small
    confirmation page so tapping it from a phone shows a friendly result."""
    e = await _create(JournalIn(ticker=ticker, type=type, strike=strike,
                                expiry=expiry, lean=lean, entry_price=entry_price,
                                source=source), session)
    price = f"${e.entry_price:,.2f}" if e.entry_price else "n/a"
    return (
        "<html><body style='font-family:sans-serif;text-align:center;padding:3em'>"
        f"<h2>✅ Saved to journal</h2>"
        f"<p><b>{e.ticker}</b> — {e.lean} idea<br>entry price {price}</p>"
        "<p>We'll track the price over the next few days so you can see how it "
        "played out.</p></body></html>"
    )


@router.post("", status_code=201)
async def add(body: JournalIn, session: AsyncSession = Depends(get_session)):
    e = await _create(body, session)
    return _serialize(e)


@router.get("")
async def list_journal(limit: int = 100, session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(JournalEntry).order_by(desc(JournalEntry.created_at)).limit(limit)
    )).scalars().all()
    return [_serialize(e) for e in rows]


@router.get("/stats")
async def stats(session: AsyncSession = Depends(get_session)):
    """Hit rate so far: how often the price went the way the idea leaned."""
    rows = (await session.execute(select(JournalEntry))).scalars().all()
    out: dict[str, dict] = {}
    for horizon, col in (("next_close", "next_close_price"), ("day3", "day3_price")):
        wins = total = 0
        for e in rows:
            p = getattr(e, col)
            if p is None or not e.entry_price:
                continue
            total += 1
            sign = 1.0 if e.lean == "bullish" else -1.0
            if (p - e.entry_price) * sign > 0:
                wins += 1
        out[horizon] = {"resolved": total, "favorable": wins,
                        "hit_rate": round(wins / total, 3) if total else None}
    return {"total_ideas": len(rows), "horizons": out}


@router.delete("/{entry_id}", status_code=204)
async def remove(entry_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(JournalEntry).where(JournalEntry.id == entry_id))
    await session.commit()
