"""Map engine probabilities to a discrete classification + confidence.

`classify()` is the single entry point used by the real-time pipeline,
the backtest, and the API.
"""
from __future__ import annotations

from app.schemas.flow import (
    Classification,
    FlowEvent,
    FlowFeatureVector,
    MarketContext,
    ScoreResult,
)
from app.scoring.engine import ScoringEngine
from app.scoring.features import build_features

_engine = ScoringEngine()


def _confidence(probs: dict[str, float], components) -> float:
    """0-100. Blends the strongest bullish probability with evidence breadth."""
    bullish = max(probs["explosion_prob"], probs["squeeze_prob"], probs["momentum_prob"])
    breadth = sum(
        1
        for v in (
            components.conviction,
            components.volume_confirmation,
            components.squeeze_fuel,
            components.catalyst,
        )
        if v > 0.5
    ) / 4.0
    raw = 0.7 * bullish + 0.3 * breadth
    raw *= 1.0 - 0.5 * probs["fake_flow_prob"]
    return round(_clamp(raw) * 100.0, 1)


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _label(probs: dict[str, float], confidence: float) -> Classification:
    # Hedging takes precedence over fake when the structure is clearly a hedge.
    if probs.get("hedging_prob", 0.0) >= 0.6 and confidence < 50:
        return Classification.HEDGING
    if probs["fake_flow_prob"] >= 0.6 and confidence < 50:
        return Classification.FAKE
    if probs["explosion_prob"] >= 0.7 and probs["squeeze_prob"] >= 0.5:
        return Classification.EXPLOSION
    if probs["momentum_prob"] >= 0.65:
        return Classification.MOMENTUM
    if confidence >= 50:
        return Classification.WATCHLIST
    if probs.get("hedging_prob", 0.0) >= 0.5:
        return Classification.INSTITUTIONAL
    if probs["fake_flow_prob"] >= 0.5:
        return Classification.HEDGING
    return Classification.NORMAL


def classify_features(features: FlowFeatureVector) -> ScoreResult:
    components, probs = _engine.score(features)
    confidence = _confidence(probs, components)
    classification = _label(probs, confidence)
    return ScoreResult(
        classification=classification,
        confidence=confidence,
        explosion_prob=probs["explosion_prob"],
        squeeze_prob=probs["squeeze_prob"],
        momentum_prob=probs["momentum_prob"],
        fake_flow_prob=probs["fake_flow_prob"],
        component_scores=components.as_dict(),
        reasons=components.reasons,
    )


def classify(
    event: FlowEvent,
    ctx: MarketContext | None = None,
    repeated_sweeps: int = 0,
) -> tuple[FlowFeatureVector, ScoreResult]:
    features = build_features(event, ctx, repeated_sweeps=repeated_sweeps)
    return features, classify_features(features)
