"""Flow + scoring endpoints."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import FlowFeatures, FlowScore, RawFlow
from app.schemas.flow import FlowEvent, MarketContext, ScoreResult
from app.scoring.classifier import classify

router = APIRouter(prefix="/flow", tags=["flow"])


@router.post("/score", response_model=ScoreResult)
async def score_event(event: FlowEvent, ctx: MarketContext | None = None):
    """Score an arbitrary flow event on demand (no persistence). Useful for
    testing the engine and for replaying historical events."""
    _, result = classify(event, ctx)
    return result


@router.get("/recent")
async def recent_flow(
    limit: int = Query(50, le=500),
    min_confidence: float = 0.0,
    min_premium: float = 0.0,
    classification: str | None = None,
    side: str | None = Query(None, description="ask / bid / mid"),
    sort: str = Query("recent", description="recent | premium"),
    session: AsyncSession = Depends(get_session),
):
    """Recent scored prints. Set sort=premium & side=ask to see the largest
    aggressive *buys* (buyer lifting the offer) — the 'big buys' feed."""
    order = desc(RawFlow.premium) if sort == "premium" else desc(RawFlow.observed_at)
    stmt = (
        select(RawFlow, FlowScore, FlowFeatures)
        .join(FlowScore, FlowScore.flow_id == RawFlow.id)
        .join(FlowFeatures, FlowFeatures.flow_id == RawFlow.id)
        .where(FlowScore.confidence >= min_confidence)
        .where(RawFlow.premium >= min_premium)
        .order_by(order)
        .limit(limit)
    )
    if classification:
        stmt = stmt.where(FlowScore.classification == classification)
    if side:
        stmt = stmt.where(RawFlow.side == side)
    rows = (await session.execute(stmt)).all()
    return [
        {
            "ticker": f.ticker,
            "contract_type": f.contract_type,
            "strike": f.strike,
            "expiry": f.expiry,
            "side": f.side,
            "size": f.size,
            "is_sweep": f.is_sweep,
            "premium": f.premium,
            "spot": f.spot,
            "observed_at": f.observed_at,
            "classification": s.classification,
            "confidence": s.confidence,
            "explosion_prob": s.explosion_prob,
            "squeeze_prob": s.squeeze_prob,
            "reasons": s.reasons.get("reasons", []),
            # Screener signals
            "iv_rank": ff.iv_rank,
            "vol_oi": ff.vol_oi,
            "is_opening": ff.is_opening,
            "bullish_structure": ff.bullish_structure >= 1.0,
            "follow_through": ff.follow_through,
        }
        for f, s, ff in rows
    ]


@router.get("/contracts")
async def contract_rollup(
    limit: int = Query(40, le=200),
    minutes: int = Query(240, le=1440, description="lookback window"),
    min_premium: float = 0.0,
    ticker: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """Aggregate prints per contract (ticker+type+strike+expiry) into
    bought (hit ask) vs sold (hit bid) volume + total premium — the
    contract-summary view. Sorted by total premium."""
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    ask_size = case((RawFlow.side == "ask", RawFlow.size), else_=0)
    bid_size = case((RawFlow.side == "bid", RawFlow.size), else_=0)
    ask_prem = case((RawFlow.side == "ask", RawFlow.premium), else_=0.0)
    sweep_n = case((RawFlow.is_sweep, 1), else_=0)

    stmt = (
        select(
            RawFlow.ticker, RawFlow.contract_type, RawFlow.strike, RawFlow.expiry,
            func.sum(RawFlow.size).label("volume"),
            func.sum(ask_size).label("bought"),
            func.sum(bid_size).label("sold"),
            func.sum(RawFlow.premium).label("premium"),
            func.sum(ask_prem).label("bought_premium"),
            func.sum(sweep_n).label("sweeps"),
            func.count().label("prints"),
            func.max(RawFlow.spot).label("spot"),
            func.max(RawFlow.observed_at).label("last_seen"),
            # Screener signals per contract (vol/OI & IV-rank are ~constant
            # across a contract's prints, so max is representative).
            func.max(FlowFeatures.vol_oi).label("vol_oi"),
            func.max(FlowFeatures.iv_rank).label("iv_rank"),
            func.bool_or(FlowFeatures.is_opening).label("is_opening"),
            func.max(FlowFeatures.bullish_structure).label("bullish_structure"),
        )
        .join(FlowFeatures, FlowFeatures.flow_id == RawFlow.id)
        .where(RawFlow.observed_at >= since)
        .group_by(RawFlow.ticker, RawFlow.contract_type, RawFlow.strike, RawFlow.expiry)
        .having(func.sum(RawFlow.premium) >= min_premium)
        .order_by(desc("premium"))
        .limit(limit)
    )
    if ticker:
        stmt = stmt.where(RawFlow.ticker == ticker.upper())
    rows = (await session.execute(stmt)).all()
    out = []
    for r in rows:
        bought, sold = int(r.bought or 0), int(r.sold or 0)
        directional = bought + sold
        out.append({
            "ticker": r.ticker,
            "contract_type": r.contract_type,
            "strike": r.strike,
            "expiry": r.expiry,
            "volume": int(r.volume or 0),
            "bought": bought,
            "sold": sold,
            "bull_pct": round(bought / directional, 3) if directional else None,
            "premium": float(r.premium or 0.0),
            "bought_premium": float(r.bought_premium or 0.0),
            "sweeps": int(r.sweeps or 0),
            "prints": int(r.prints or 0),
            "spot": r.spot,
            "last_seen": r.last_seen,
            "vol_oi": float(r.vol_oi) if r.vol_oi is not None else None,
            "iv_rank": float(r.iv_rank) if r.iv_rank is not None else None,
            "is_opening": bool(r.is_opening),
            "bullish_structure": (r.bullish_structure or 0) >= 1.0,
        })
    return out
