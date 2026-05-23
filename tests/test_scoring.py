"""Scoring-engine sanity checks — these encode the product's core intent."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas.flow import (
    Classification,
    ContractType,
    FlowEvent,
    MarketContext,
    Side,
)
from app.scoring.classifier import classify


def _event(**kw) -> FlowEvent:
    base = dict(
        source="test",
        ticker="GME",
        contract_type=ContractType.CALL,
        strike=22.0,
        expiry=datetime.now(timezone.utc) + timedelta(days=10),
        side=Side.ASK,
        is_sweep=True,
        premium=750_000,
        size=2000,
        spot=20.0,
        observed_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    return FlowEvent(**base)


def test_explosive_setup_scores_high():
    ctx = MarketContext(
        ticker="GME",
        float_shares=8e6,
        short_interest_pct=30,
        borrow_rate=80,
        rel_options_volume=9,
        stock_rvol=4,
        oi_change_ratio=1.3,
        dealer_gamma=-0.8,
        social_score=0.9,
        news_score=0.8,
        historical_similarity=0.9,
    )
    _, result = classify(_event(), ctx, repeated_sweeps=4)
    assert result.confidence > 70
    assert result.explosion_prob > 0.5
    assert result.classification in {
        Classification.EXPLOSION,
        Classification.MOMENTUM,
    }
    assert result.reasons


def test_midpoint_low_volume_flagged_as_fake():
    ctx = MarketContext(ticker="AAPL", float_shares=16e9, short_interest_pct=1,
                        rel_options_volume=1.0, stock_rvol=0.9)
    _, result = classify(_event(ticker="AAPL", side=Side.MID, is_sweep=False,
                                premium=40_000), ctx)
    assert result.fake_flow_prob > 0.5
    assert result.confidence < 70


def test_unknown_side_unusual_flow_not_fake_and_alerts():
    """An options-only data plan has no aggressor side (event.side is None).
    Such flow must NOT be auto-flagged as fake/hedging, and a strongly unusual
    print (high vol/OI, big premium, opening, near-dated) should still alert via
    the unusual-activity gate."""
    from app.alerts.base import AlertDispatcher

    ev = _event(side=None, is_sweep=False, premium=900_000, size=3000,
                open_interest=300, vol_oi=6.0)
    feats, result = classify(ev, MarketContext(ticker="GME"))
    assert feats.aggressor_known is False
    assert result.fake_flow_prob < 0.6          # unknown side != sold
    d = AlertDispatcher()
    assert d.should_alert(result, ev, feats)    # unusual-activity gate fires
    assert d.is_unusual_activity_only(result, ev, feats)


def test_known_bid_side_still_penalised():
    """A genuine sold (bid-side) print is still treated as low conviction."""
    _, result = classify(_event(side=Side.BID, is_sweep=False, premium=60_000),
                         MarketContext(ticker="GME", rel_options_volume=1.0))
    assert result.classification in {Classification.NORMAL, Classification.FAKE,
                                     Classification.HEDGING}


def test_alert_gate_suppresses_then_cooldowns(monkeypatch):
    """Startup grace blocks the boot-time backlog; after grace a contract alerts
    once, then is cooled down."""
    import app.alerts.base as base
    from app.config import settings

    monkeypatch.setattr(settings, "alert_startup_grace_sec", 0.0)
    monkeypatch.setattr(settings, "alert_cooldown_min", 60.0)
    gate = base.AlertGate()
    ev = _event()
    assert gate.allow(ev) is True       # first one passes
    assert gate.allow(ev) is False      # same contract is cooled down

    # A fresh gate still in its grace window suppresses everything.
    monkeypatch.setattr(settings, "alert_startup_grace_sec", 9999.0)
    assert base.AlertGate().allow(ev) is False


def test_probabilities_in_range():
    _, result = classify(_event(), MarketContext(ticker="GME"))
    for p in (result.explosion_prob, result.squeeze_prob,
              result.momentum_prob, result.fake_flow_prob):
        assert 0.0 <= p <= 1.0
    assert 0.0 <= result.confidence <= 100.0
