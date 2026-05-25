"""On-demand single-ticker technical analysis."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.llm_analyst import ai_analyst_take
from app.analysis.technicals import analyze
from app.config import settings
from app.core.logging import get_logger
from app.db.base import get_session
from app.db.models import AnalystLevels

router = APIRouter(prefix="/analysis", tags=["analysis"])
log = get_logger("api.analysis")


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


@router.get("/{ticker}/ai")
async def ai_take(ticker: str):
    """Claude's plain-English analyst take, reasoning over the flow + technicals
    + your analyst levels. Requires ANTHROPIC_API_KEY."""
    if not settings.anthropic_api_key:
        raise HTTPException(503, "AI analyst is disabled — set ANTHROPIC_API_KEY.")
    try:
        return await ai_analyst_take(ticker)
    except Exception as exc:  # noqa: BLE001
        log.error("ai take failed", ticker=ticker, error=str(exc))
        raise HTTPException(502, f"AI analyst error: {exc}") from exc
