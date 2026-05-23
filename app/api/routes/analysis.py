"""On-demand single-ticker technical analysis."""
from __future__ import annotations

from fastapi import APIRouter

from app.analysis.technicals import analyze

router = APIRouter(prefix="/analysis", tags=["analysis"])


@router.get("/{ticker}")
async def technical_analysis(ticker: str):
    """Trend, moving averages, RSI/MACD, support/resistance and suggested
    price-alert levels for a ticker — built from real Polygon daily bars."""
    return await analyze(ticker)
