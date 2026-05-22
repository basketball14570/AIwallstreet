"""Regime detection + its effect on scoring and the high-confidence metric."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np

from app.scoring.regime import MacroContext, NEUTRAL, detect_regime
from app.scoring.classifier import classify_features
from app.schemas.flow import FlowFeatureVector
from app.scoring.similarity import HistoricalLibrary


def _strong_features() -> FlowFeatureVector:
    return FlowFeatureVector(
        ask_side_ratio=1.0, sweep_urgency=0.9, repeated_sweeps=4, otm_pct=0.08,
        dte=10, rel_options_volume=9, stock_rvol=4, oi_change_ratio=1.3,
        float_shares=8e6, short_interest_pct=30, borrow_rate=80, dealer_gamma=-0.8,
        social_score=0.9, news_score=0.8, historical_similarity=0.9,
    )


def test_detect_risk_off():
    m = MacroContext(vix=32, vix_trend=0.25, breadth=0.3, putcall_skew=1.3)
    assert detect_regime(m).name == "risk_off"


def test_detect_low_vol_chop():
    m = MacroContext(vix=12, smallcap_rs=0.98)
    assert detect_regime(m).name == "low_vol_chop"


def test_detect_squeeze_friendly():
    m = MacroContext(vix=22, aggregate_gamma=-0.5, smallcap_rs=1.05)
    assert detect_regime(m).name == "high_vol_squeeze"


def test_no_macro_is_neutral():
    assert detect_regime(MacroContext()) is NEUTRAL


def test_regime_damps_confidence_in_risk_off():
    f = _strong_features()
    base = classify_features(f, NEUTRAL)
    risk_off = detect_regime(MacroContext(vix=32, vix_trend=0.25, breadth=0.3))
    damped = classify_features(f, risk_off)
    assert damped.confidence < base.confidence
    assert damped.explosion_prob < base.explosion_prob
    assert any("Regime" in r for r in damped.reasons)


def test_regime_neutral_is_identity():
    f = _strong_features()
    assert classify_features(f, NEUTRAL).confidence == classify_features(f).confidence


def test_library_named_analogs():
    rng = np.random.default_rng(0)
    X = np.vstack([rng.normal(2, 0.2, (50, 16)), rng.normal(-2, 0.2, (50, 16))])
    y = np.array([1] * 50 + [0] * 50)
    names = [f"GME 2024-{i:02d}-01" for i in range(50)] + [f"AAPL row {i}" for i in range(50)]
    lib = HistoricalLibrary.fit(X, y, names)
    hit, analogs = lib.query_with_analogs(np.full(16, 2.0), top=3)
    assert hit > 0.8
    assert len(analogs) == 3
    assert all(t.startswith("GME") for t, _ in analogs)
