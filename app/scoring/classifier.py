"""Map engine probabilities to a discrete classification + confidence.

`classify()` is the single entry point used by the real-time pipeline,
the backtest, and the API.
"""
from __future__ import annotations

import os
import time

from app.schemas.flow import (
    Classification,
    FlowEvent,
    FlowFeatureVector,
    MarketContext,
    ScoreResult,
)
from app.scoring.engine import ScoringEngine
from app.scoring.features import build_features
from app.scoring.regime import NEUTRAL, Regime
from app.scoring.sequence import SequenceFeatures

_engine = ScoringEngine()

# Optional historical k-NN library; lazily loaded and hot-reloaded when the
# nightly retrain rewrites the artifact (mtime-checked, throttled).
_LIBRARY_PATH = "models/historical_library.npz"
_RELOAD_INTERVAL = 30.0
_UNSET = object()
_library = None
_lib_mtime = _UNSET
_lib_checked = 0.0


def _get_library():
    global _library, _lib_mtime, _lib_checked
    now = time.monotonic()
    if now - _lib_checked >= _RELOAD_INTERVAL:
        _lib_checked = now
        try:
            m = os.path.getmtime(_LIBRARY_PATH)
        except OSError:
            m = None
        if m != _lib_mtime:
            from app.scoring.similarity import HistoricalLibrary

            _lib_mtime = m
            _library = HistoricalLibrary.load(_LIBRARY_PATH) if m else None
    return _library


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


def _apply_regime(probs: dict[str, float], regime: Regime) -> None:
    """Scale bullish probabilities in place by the regime multiplier."""
    if regime.bullish_multiplier == 1.0:
        return
    for k in ("explosion_prob", "squeeze_prob", "momentum_prob"):
        probs[k] = round(_clamp(probs[k] * regime.bullish_multiplier), 4)


def classify_features(
    features: FlowFeatureVector, regime: Regime = NEUTRAL
) -> ScoreResult:
    components, probs = _engine.score(features)
    _apply_regime(probs, regime)
    confidence = _clamp(_confidence(probs, components) * regime.confidence_scale, 0, 100)
    confidence = round(confidence, 1)
    reasons = list(components.reasons)
    if regime.name != "neutral" and regime.reason:
        reasons.append(f"Regime: {regime.reason}")
    classification = _label(probs, confidence)
    return ScoreResult(
        classification=classification,
        confidence=confidence,
        explosion_prob=probs["explosion_prob"],
        squeeze_prob=probs["squeeze_prob"],
        momentum_prob=probs["momentum_prob"],
        fake_flow_prob=probs["fake_flow_prob"],
        component_scores=components.as_dict(),
        reasons=reasons,
        regime=regime.name,
    )


def classify(
    event: FlowEvent,
    ctx: MarketContext | None = None,
    repeated_sweeps: int = 0,
    regime: Regime = NEUTRAL,
    seq: SequenceFeatures | None = None,
    follow_through: float = 0.0,
    iv_rank: float | None = None,
) -> tuple[FlowFeatureVector, ScoreResult]:
    features = build_features(event, ctx, repeated_sweeps=repeated_sweeps, seq=seq,
                              follow_through=follow_through, iv_rank=iv_rank)
    analogs: list[tuple[str, float]] = []
    lib = _get_library()
    if lib is not None:
        from app.scoring.similarity import vector_from_features

        sim, analogs = lib.query_with_analogs(vector_from_features(features))
        features.historical_similarity = sim
    result = classify_features(features, regime)
    if analogs:
        names = ", ".join(f"{t} ({s:.0%})" for t, s in analogs)
        result.reasons.append(f"Resembles past explosive setups: {names}")
    return features, result
