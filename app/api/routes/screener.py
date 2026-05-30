"""Self-driven technical breakout screener endpoints.

Surfaces the stocks the system finds *on its own* — coiling for an expansion
from price/volume technicals — ranked, with options-flow confluence flagged.
"""
from __future__ import annotations

from fastapi import APIRouter, Query

from app.analysis.screener import scan_ticker
from app.config import settings
from app.core.logging import get_logger
from app.jobs.screener_scan import run_screen

router = APIRouter(prefix="/screener", tags=["screener"])
log = get_logger("api.screener")


@router.get("")
async def screen(
    top_n: int = Query(default=None, ge=1, le=100,
                       description="How many ranked candidates to return."),
    min_score: float = Query(default=None, ge=0, le=100,
                             description="Minimum composite setup score (0-100)."),
    include_flow: bool = Query(True, description="Cross-reference unusual call flow for confluence."),
):
    """Sweep the configured universe and return the stocks mechanically coiling
    for a breakout, best-first. ``🔥`` confluence names also show fresh unusual
    call buying. Universe + thresholds are configured in settings."""
    return await run_screen(top_n=top_n, min_score=min_score, include_flow=include_flow)


@router.get("/{ticker}")
async def screen_one(ticker: str):
    """The technical breakout read for a single ticker (the same scoring the
    universe sweep uses), regardless of score."""
    result = await scan_ticker(ticker)
    if result is None:
        return {"ticker": ticker.upper(), "data_source": "unavailable",
                "error": "Not enough price history to score this ticker."}
    return result
