"""On-demand single-ticker technical analysis."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.technicals import analyze
from app.db.base import get_session
from app.db.models import AnalystLevels

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/{ticker}")
async def technical_analysis(ticker: str, session: AsyncSession = Depends(get_session)):
    """Trend, moving averages, RSI/MACD, support/resistance and suggested
    price-alert levels for a ticker — built from real Polygon daily bars, plus
    any trusted analyst levels imported for the ticker."""
    result = await analyze(ticker)
    a = await session.get(AnalystLevels, ticker.upper())
    if a:
        result["analyst"] = {"clb36": a.clb36, "weekly_cpl": a.weekly_cpl,
                             "resistances": a.resistances, "supports": a.supports}
    return result
