"""Alert utilities — a test endpoint to verify delivery + preview the format."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from app.alerts.base import AlertDispatcher
from app.alerts.summary import generate_summary
from app.analysis.levels import levels_block
from app.core.logging import get_logger
from app.providers.prices import PriceHistoryProvider
from app.schemas.flow import (
    Classification, ContractType, FlowEvent, ScoreResult, Side,
)

router = APIRouter(prefix="/alerts", tags=["alerts"])
log = get_logger("api.alerts")
_prices = PriceHistoryProvider()


async def _recent_spot(ticker: str) -> float:
    """Last close as a stand-in spot. Falls back to 100 if price history is
    unavailable (e.g. a plan without stock aggregates) so the delivery test
    still works."""
    try:
        end = datetime.now(timezone.utc)
        closes = await _prices.daily_closes(ticker, end - timedelta(days=10), end)
        if len(closes):
            return round(float(closes.iloc[-1]), 2)
    except Exception as exc:  # noqa: BLE001
        log.warning("test spot lookup failed", ticker=ticker, error=str(exc))
    return 100.0


def _sample(ticker: str, spot: float) -> tuple[FlowEvent, ScoreResult]:
    now = datetime.now(timezone.utc)
    event = FlowEvent(
        source="test", ticker=ticker.upper(), contract_type=ContractType.CALL,
        strike=round(spot * 1.05, 0), expiry=now + timedelta(days=30),
        side=Side.ASK, is_sweep=True, premium=750_000, size=2_000, spot=spot,
        iv=0.55, open_interest=500, vol_oi=3.0, observed_at=now,
    )
    result = ScoreResult(
        classification=Classification.EXPLOSION, confidence=82.0,
        explosion_prob=0.71, squeeze_prob=0.42, momentum_prob=0.63, fake_flow_prob=0.08,
        component_scores={"follow_through": 0.7},
        reasons=[
            "TEST alert — sample explosion setup (not real flow)",
            "Aggressive ask-side sweep — buyer paying up",
            "Volume 3x open interest — new position, not closing",
        ],
    )
    return event, result


@router.get("/test")
async def test_alert(ticker: str = Query("NVDA", description="ticker to preview")):
    """Build a sample explosion alert (with real chart levels for the ticker)
    and dispatch it to every configured channel. Visit in a browser to confirm
    Telegram/Discord delivery and see exactly what an alert looks like.

    Returns the rendered text plus per-channel status ('sent' / 'skipped' /
    'error'). 'skipped' means that channel isn't configured in your .env."""
    spot = await _recent_spot(ticker)
    event, result = _sample(ticker, spot)
    status = await AlertDispatcher().dispatch(event, result)
    preview = generate_summary(event, result)
    try:
        block = await levels_block(event)
    except Exception as exc:  # noqa: BLE001
        log.warning("levels preview failed", ticker=ticker, error=str(exc))
        block = ""
    if block:
        preview = f"{preview}\n\n{block}"
    return {"ticker": ticker.upper(), "spot": spot, "channels": status,
            "levels_available": bool(block), "preview": preview}
