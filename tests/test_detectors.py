"""Tests for the specialised detectors and calibration."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas.flow import (
    Classification,
    ContractType,
    FlowEvent,
    MarketContext,
    Side,
)
from app.scoring.calibration import Calibrator, expected_calibration_error
from app.scoring.classifier import classify
from app.scoring.normalize import logistic, ramp, soft_and


def _event(**kw) -> FlowEvent:
    base = dict(
        source="test", ticker="X", contract_type=ContractType.CALL, strike=22.0,
        expiry=datetime.now(timezone.utc) + timedelta(days=10), side=Side.ASK,
        is_sweep=True, premium=600_000, size=2000, spot=20.0,
        observed_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    return FlowEvent(**base)


def test_institutional_hedge_detected_for_megacap_spread_put():
    ctx = MarketContext(ticker="AAPL", float_shares=15e9, short_interest_pct=1,
                        rel_options_volume=1.2, stock_rvol=1.0)
    ev = _event(ticker="AAPL", contract_type=ContractType.PUT, strike=200,
                spot=200, is_spread=True, side=Side.MID, is_sweep=False,
                premium=1_500_000, expiry=datetime.now(timezone.utc) + timedelta(days=60))
    _, result = classify(ev, ctx)
    assert result.classification in {Classification.HEDGING, Classification.INSTITUTIONAL}
    assert result.component_scores["institutional_hedging"] >= 0.6
    assert result.explosion_prob < 0.3


def test_gamma_squeeze_requires_negative_dealer_gamma():
    pos = MarketContext(ticker="X", float_shares=20e6, dealer_gamma=0.9,
                        rel_options_volume=8, stock_rvol=3)
    neg = MarketContext(ticker="X", float_shares=20e6, dealer_gamma=-0.9,
                        rel_options_volume=8, stock_rvol=3)
    _, r_pos = classify(_event(), pos)
    _, r_neg = classify(_event(), neg)
    assert r_neg.component_scores["gamma_squeeze"] > r_pos.component_scores["gamma_squeeze"]
    assert r_pos.component_scores["gamma_squeeze"] == 0.0


def test_soft_and_punishes_single_axis():
    # One strong leg, two weak -> low. All three strong -> high.
    assert soft_and(0.9, 0.05, 0.05) < 0.3
    assert soft_and(0.8, 0.8, 0.8) > 0.75


def test_calibrator_fit_recovers_separable_signal():
    scores = [i / 100 for i in range(100)]
    labels = [1 if s > 0.5 else 0 for s in scores]
    cal = Calibrator.fit_platt(scores, labels, iters=300, lr=0.5)
    assert cal(0.9) > cal(0.1)
    ece = expected_calibration_error([cal(s) for s in scores], labels)
    assert 0.0 <= ece <= 1.0


def test_normalize_bounds():
    assert 0.0 <= logistic(1e9, 0, 1) <= 1.0
    assert ramp(5, 0, 10) == 0.5
    assert ramp(-3, 0, 10) == 0.0
