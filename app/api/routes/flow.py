"""Flow + scoring endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_session
from app.db.models import FlowScore, RawFlow
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
    classification: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(RawFlow, FlowScore)
        .join(FlowScore, FlowScore.flow_id == RawFlow.id)
        .where(FlowScore.confidence >= min_confidence)
        .order_by(desc(RawFlow.observed_at))
        .limit(limit)
    )
    if classification:
        stmt = stmt.where(FlowScore.classification == classification)
    rows = (await session.execute(stmt)).all()
    return [
        {
            "ticker": f.ticker,
            "contract_type": f.contract_type,
            "strike": f.strike,
            "expiry": f.expiry,
            "premium": f.premium,
            "observed_at": f.observed_at,
            "classification": s.classification,
            "confidence": s.confidence,
            "explosion_prob": s.explosion_prob,
            "squeeze_prob": s.squeeze_prob,
            "reasons": s.reasons.get("reasons", []),
        }
        for f, s in rows
    ]
