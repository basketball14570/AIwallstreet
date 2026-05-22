"""Tests for the screener additions: IV-rank, vol/OI opening, earnings
proximity, multi-leg structure awareness, follow-through and ticker priors."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from app.alerts.base import AlertDispatcher
from app.config import settings
from app.pipeline.realtime import FollowThroughTracker, IVRankTracker
from app.schemas.flow import (
    Classification,
    ContractType,
    FlowEvent,
    FlowFeatureVector,
    MarketContext,
    ScoreResult,
    Side,
    Structure,
)
from app.scoring.classifier import classify
from app.scoring.priors import TickerPriors, shrink_hit_rate


def _event(**kw) -> FlowEvent:
    base = dict(
        source="test", ticker="GME", contract_type=ContractType.CALL, strike=22.0,
        expiry=datetime.now(timezone.utc) + timedelta(days=10), side=Side.ASK,
        is_sweep=True, premium=600_000, size=2000, spot=20.0,
        observed_at=datetime.now(timezone.utc),
    )
    base.update(kw)
    return FlowEvent(**base)


def _ctx(**kw) -> MarketContext:
    base = dict(ticker="GME", float_shares=10e6, short_interest_pct=20,
                rel_options_volume=6, stock_rvol=3)
    base.update(kw)
    return MarketContext(**base)


# ----- vol / open-interest "opening" ---------------------------------------
def test_high_vol_oi_lifts_volume_confirmation():
    feat_lo, lo = classify(_event(vol_oi=0.1, open_interest=50000), _ctx())
    feat_hi, hi = classify(_event(vol_oi=6.0, open_interest=300), _ctx())
    assert feat_hi.is_opening and not feat_lo.is_opening
    assert hi.component_scores["volume_confirmation"] > lo.component_scores["volume_confirmation"]


# ----- IV-rank --------------------------------------------------------------
def test_cheap_iv_adds_value_rich_iv_flags_chasing():
    _, cheap = classify(_event(), _ctx(), iv_rank=0.1)
    _, rich = classify(_event(), _ctx(), iv_rank=0.97)
    assert cheap.component_scores["iv_value"] > rich.component_scores["iv_value"]
    assert any("cheap IV" in r for r in cheap.reasons)
    assert any("richly-priced IV" in r for r in rich.reasons)
    # Chasing rich IV nudges fake-flow up relative to cheap IV.
    assert rich.fake_flow_prob >= cheap.fake_flow_prob


def test_unknown_iv_is_neutral():
    # iv_rank None -> 0.5; iv_value ~0.5 and no IV reason fired.
    feats, res = classify(_event(), _ctx(), iv_rank=None)
    assert feats.iv_rank == 0.5
    assert not any("IV" in r for r in res.reasons)


# ----- earnings proximity ---------------------------------------------------
def test_imminent_earnings_boosts_catalyst():
    _, near = classify(_event(), _ctx(days_to_earnings=2, social_score=0.0, news_score=0.0))
    _, far = classify(_event(), _ctx(days_to_earnings=120, social_score=0.0, news_score=0.0))
    assert near.component_scores["catalyst"] > far.component_scores["catalyst"]
    assert any("Earnings in" in r for r in near.reasons)


# ----- multi-leg structure awareness ---------------------------------------
def test_bullish_structure_not_treated_as_hedge():
    bull = _event(is_spread=True, structure=Structure.CALL_VERTICAL)
    neutral = _event(is_spread=True, structure=Structure.CONDOR)
    _, rb = classify(bull, _ctx())
    _, rn = classify(neutral, _ctx())
    assert rb.component_scores["institutional_hedging"] < rn.component_scores["institutional_hedging"]
    # The bullish vertical should not be heavily damped into a hedge bucket.
    assert rb.explosion_prob > rn.explosion_prob


# ----- follow-through -------------------------------------------------------
def test_follow_through_raises_conviction():
    _, none = classify(_event(), _ctx(), follow_through=0.0)
    _, built = classify(_event(), _ctx(), follow_through=0.9)
    assert built.component_scores["conviction"] > none.component_scores["conviction"]
    assert any("followed through" in r for r in built.reasons)


# ----- ticker hit-rate prior ------------------------------------------------
def test_ticker_prior_lifts_explosion():
    _, cold = classify(_event(), _ctx(ticker_hit_rate=0.0))
    _, hot = classify(_event(), _ctx(ticker_hit_rate=0.9))
    assert hot.explosion_prob >= cold.explosion_prob
    assert any("hit-rate" in r for r in hot.reasons)


# ----- trackers -------------------------------------------------------------
def test_iv_rank_tracker_ranks_within_range():
    t = IVRankTracker(min_history=3)
    base = dict(source="s", ticker="X", contract_type=ContractType.CALL, strike=1,
                expiry=datetime.now(timezone.utc) + timedelta(days=5),
                premium=1, size=1, observed_at=datetime.now(timezone.utc))
    now = 1000.0
    for iv in (0.2, 0.4, 0.6, 0.8):
        t.record(FlowEvent(iv=iv, **base), now=now)
        now += 1
    # A new low IV ranks near 0, a new high near 1.
    assert t.record(FlowEvent(iv=0.2, **base), now=now) < 0.2
    assert t.record(FlowEvent(iv=0.8, **base), now=now + 1) > 0.8


def test_iv_rank_tracker_none_without_iv_or_history():
    t = IVRankTracker(min_history=5)
    ev = _event(iv=None)
    assert t.record(ev) is None  # no IV


def test_follow_through_builds_with_repeated_prints():
    t = FollowThroughTracker()
    ev = _event(premium=500_000)
    first = t.record(ev, now=1000.0)
    assert first == 0.0  # nothing preceded it
    second = t.record(ev, now=1001.0)
    third = t.record(ev, now=1002.0)
    assert 0.0 < second <= third


# ----- priors ---------------------------------------------------------------
def test_shrink_hit_rate_pulls_small_samples_to_global():
    # 1/1 winners shrinks well below 1.0 toward a low global rate.
    assert shrink_hit_rate(1, 1, global_rate=0.1) < 0.5
    # Large sample converges to the raw rate.
    assert abs(shrink_hit_rate(800, 1000, global_rate=0.1) - 0.8) < 0.05


# ----- alert gating ---------------------------------------------------------
def _passing_result() -> ScoreResult:
    return ScoreResult(
        classification=Classification.MOMENTUM, confidence=85.0,
        explosion_prob=0.6, squeeze_prob=0.4, momentum_prob=0.8, fake_flow_prob=0.1,
        component_scores={}, reasons=[],
    )


def _otm_call() -> FlowEvent:
    return _event(strike=25.0, spot=20.0)  # OTM call so the existing gate passes


def test_alert_passes_without_feature_gates():
    d = AlertDispatcher()
    assert d.should_alert(_passing_result(), _otm_call(),
                          FlowFeatureVector(is_opening=True, iv_rank=0.3))


def test_require_opening_gate(monkeypatch):
    monkeypatch.setattr(settings, "alert_require_opening", True)
    d = AlertDispatcher()
    res, ev = _passing_result(), _otm_call()
    assert not d.should_alert(res, ev, FlowFeatureVector(is_opening=False))
    assert d.should_alert(res, ev, FlowFeatureVector(is_opening=True))
    # No features => gate cannot apply, alert still passes.
    assert d.should_alert(res, ev)


def test_max_iv_rank_gate(monkeypatch):
    monkeypatch.setattr(settings, "alert_max_iv_rank", 0.8)
    d = AlertDispatcher()
    res, ev = _passing_result(), _otm_call()
    assert not d.should_alert(res, ev, FlowFeatureVector(iv_rank=0.95))
    assert d.should_alert(res, ev, FlowFeatureVector(iv_rank=0.2))


def test_max_iv_rank_gate_exempts_unknown(monkeypatch):
    # Threshold below the 0.5 sentinel must NOT filter unknown-IV contracts.
    monkeypatch.setattr(settings, "alert_max_iv_rank", 0.4)
    d = AlertDispatcher()
    res, ev = _passing_result(), _otm_call()
    assert d.should_alert(res, ev, FlowFeatureVector(iv_rank=0.5))   # unknown -> exempt
    assert not d.should_alert(res, ev, FlowFeatureVector(iv_rank=0.6))  # real, above cap


def test_bullish_structures_only_gate(monkeypatch):
    monkeypatch.setattr(settings, "alert_bullish_structures_only", True)
    d = AlertDispatcher()
    res = _passing_result()
    neutral = _event(strike=25.0, spot=20.0, is_spread=True, structure=Structure.CONDOR)
    bullish = _event(strike=25.0, spot=20.0, is_spread=True, structure=Structure.CALL_VERTICAL)
    single = _otm_call()  # not a spread -> unaffected by the gate
    assert not d.should_alert(res, neutral)
    assert d.should_alert(res, bullish)
    assert d.should_alert(res, single)


# ----- Polygon snapshot pagination -----------------------------------------
def test_polygon_snapshot_paginates_and_keeps_top_volume(monkeypatch):
    import asyncio

    from app.core.ratelimit import TokenBucket
    from app.providers.polygon_flow import PolygonFlowProvider

    class _Resp:
        def __init__(self, body):
            self._b, self.status_code = body, 200

        def raise_for_status(self):
            pass

        def json(self):
            return self._b

    class _Client:
        def __init__(self, pages):
            self.pages, self.calls = pages, []

        async def get(self, url, params=None, timeout=None):
            self.calls.append((url, params))
            return _Resp(self.pages.pop(0))

    def vol(v):
        return {"day": {"volume": v}}

    pages = [
        {"results": [vol(5), vol(1)], "next_url": "u2"},
        {"results": [vol(9)], "next_url": "u3"},
        {"results": [vol(3)]},  # no next_url -> stop
    ]
    monkeypatch.setattr(settings, "polygon_flow_max_pages", 5)
    monkeypatch.setattr(settings, "polygon_flow_top_contracts", 2)

    p = PolygonFlowProvider.__new__(PolygonFlowProvider)
    p._bucket = TokenBucket(rate=1000, capacity=1000)
    client = _Client(list(pages))

    out = asyncio.run(p._snapshot(client, "NVDA"))
    # Followed next_url across all 3 pages, then kept the 2 highest volumes.
    assert len(client.calls) == 3
    assert [c["day"]["volume"] for c in out] == [9, 5]
    # The cursor follow-ups resend only the apiKey, not limit.
    assert "limit" not in client.calls[1][1]


def test_build_ticker_priors_roundtrip(tmp_path):
    from app.jobs.retrain import build_ticker_priors

    df = pd.DataFrame({
        "ticker": ["GME"] * 30 + ["AAPL"] * 30,
        "label": [1] * 25 + [0] * 5 + [0] * 28 + [1] * 2,
    })
    priors = build_ticker_priors(df)
    assert priors.get("GME") > priors.get("AAPL")
    p = tmp_path / "priors.json"
    priors.save(p)
    loaded = TickerPriors.load(p)
    assert loaded.get("GME") == priors.get("GME")
