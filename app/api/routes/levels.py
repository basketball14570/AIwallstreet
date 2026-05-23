"""Trusted analyst weekly levels: import (paste), list, fetch, delete."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.analyst_levels import parse_levels_table
from app.db.base import get_session
from app.db.models import AnalystLevels

router = APIRouter(prefix="/levels", tags=["levels"])


class ImportIn(BaseModel):
    text: str


def _serialize(a: AnalystLevels) -> dict:
    return {"ticker": a.ticker, "clb36": a.clb36, "weekly_cpl": a.weekly_cpl,
            "resistances": a.resistances, "supports": a.supports,
            "updated_at": a.updated_at.isoformat() if a.updated_at else None}


@router.post("/import")
async def import_levels(body: ImportIn, session: AsyncSession = Depends(get_session)):
    """Paste the analyst's weekly table. Each ticker is upserted (re-import wins)."""
    parsed = parse_levels_table(body.text)
    for row in parsed:
        await session.merge(AnalystLevels(**row))
    await session.commit()
    return {"imported": len(parsed), "tickers": [r["ticker"] for r in parsed]}


@router.get("")
async def list_levels(session: AsyncSession = Depends(get_session)):
    rows = (await session.execute(
        select(AnalystLevels).order_by(AnalystLevels.ticker)
    )).scalars().all()
    return [_serialize(a) for a in rows]


@router.get("/{ticker}")
async def get_levels(ticker: str, session: AsyncSession = Depends(get_session)):
    a = await session.get(AnalystLevels, ticker.upper())
    return _serialize(a) if a else {}


@router.delete("/{ticker}", status_code=204)
async def delete_levels(ticker: str, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(AnalystLevels).where(AnalystLevels.ticker == ticker.upper()))
    await session.commit()
