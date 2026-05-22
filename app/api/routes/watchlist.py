"""Watchlist CRUD."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import Watchlist

router = APIRouter(prefix="/watchlist", tags=["watchlist"])


class WatchlistIn(BaseModel):
    ticker: str
    note: str | None = None


@router.get("")
async def list_watchlist(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(select(Watchlist))).scalars().all()
    return [{"ticker": w.ticker, "note": w.note} for w in rows]


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
