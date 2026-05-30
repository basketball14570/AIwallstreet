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


@router.get("/{ticker}/levels")
async def support_resistance(ticker: str, session: AsyncSession = Depends(get_session)):
    """Ranked support/resistance zones plus a level-anchored options playbook —
    when a break above resistance argues for calls, when a hold at support does,
    when a breakdown/rejection argues for puts — each with trigger, target, stop,
    reward:risk and a strike to watch. Built from real daily bars. Educational,
    not financial advice."""
    ta = await analyze(ticker)
    if ta.get("data_source") == "unavailable":
        return {"ticker": ticker.upper(), "data_source": "unavailable",
                "error": ta.get("error")}
    out = {
        "ticker": ta["ticker"], "spot": ta.get("spot"), "current": ta.get("current"),
        "trend": ta.get("trend"), "atr": ta.get("atr"),
        "typical_move_pct": ta.get("typical_move_pct"),
        "sr_levels": ta.get("sr_levels", []), "playbook": ta.get("playbook", []),
        "nearest_resistance": ta.get("nearest_resistance"),
        "nearest_support": ta.get("nearest_support"),
        "data_source": ta.get("data_source"),
    }
    try:  # analyst-levels enrichment is optional — never block the playbook on it
        a = await session.get(AnalystLevels, ticker.upper())
        if a:
            out["analyst"] = {"resistances": a.resistances, "supports": a.supports}
    except Exception as exc:  # noqa: BLE001
        log.warning("analyst-levels lookup failed", ticker=ticker, error=str(exc))
    return out


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
