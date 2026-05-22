"""Turn a raw FlowEvent + MarketContext into a normalised feature vector.

Keeps all unit conversions and derived ratios in one place so the scoring
engine and the ML model consume identical features.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.schemas.flow import (
    ContractType,
    FlowEvent,
    FlowFeatureVector,
    MarketContext,
    Side,
)
from app.scoring.sequence import SequenceFeatures


def _dte(expiry: datetime, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    return max((expiry - now).total_seconds() / 86400.0, 0.0)


def _otm_pct(event: FlowEvent) -> float:
    """Percent out-of-the-money. Positive = OTM (bullish-leaning for calls)."""
    if not event.spot or event.spot <= 0:
        return 0.0
    if event.contract_type == ContractType.CALL:
        return (event.strike - event.spot) / event.spot
    return (event.spot - event.strike) / event.spot


def _sweep_urgency(event: FlowEvent) -> float:
    """0-1 urgency proxy: sweeps lifting the ask with large premium are urgent."""
    score = 0.0
    if event.is_sweep:
        score += 0.5
    if event.side == Side.ASK:
        score += 0.3
    # premium bucket: $250k+ adds urgency
    score += min(event.premium / 1_000_000.0, 1.0) * 0.2
    return min(score, 1.0)


def build_features(
    event: FlowEvent,
    ctx: MarketContext | None,
    repeated_sweeps: int = 0,
    now: datetime | None = None,
    seq: SequenceFeatures | None = None,
) -> FlowFeatureVector:
    ctx = ctx or MarketContext(ticker=event.ticker)
    seq = seq or SequenceFeatures()
    ask_side_ratio = 1.0 if event.side == Side.ASK else (0.5 if event.side == Side.MID else 0.0)
    return FlowFeatureVector(
        seq_cadence_accel=seq.cadence_accel,
        seq_strike_ladder=seq.strike_ladder,
        seq_premium_velocity=seq.premium_velocity,
        seq_count=seq.count,
        ask_side_ratio=ask_side_ratio,
        sweep_urgency=_sweep_urgency(event),
        repeated_sweeps=repeated_sweeps,
        at_midpoint=event.side == Side.MID,
        is_put=event.contract_type == ContractType.PUT,
        is_spread=event.is_spread,
        premium=event.premium,
        otm_pct=_otm_pct(event),
        dte=_dte(event.expiry, now),
        rel_options_volume=ctx.rel_options_volume or 0.0,
        stock_rvol=ctx.stock_rvol or 0.0,
        oi_change_ratio=ctx.oi_change_ratio or 0.0,
        float_shares=ctx.float_shares,
        short_interest_pct=ctx.short_interest_pct,
        borrow_rate=ctx.borrow_rate,
        dealer_gamma=ctx.dealer_gamma,
        social_score=ctx.social_score,
        news_score=ctx.news_score,
        historical_similarity=ctx.historical_similarity,
    )
