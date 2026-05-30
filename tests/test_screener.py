"""Tests for the self-driven technical breakout screener.

The scoring is a pure function over a daily-OHLCV frame, so these run fully
offline against deterministic synthetic charts.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd

from app.analysis.screener import (
    CLS_NONE,
    evaluate,
    scan_universe,
)
from app.jobs.screener_scan import run_screen


def _bars(close: np.ndarray, vol: np.ndarray | None = None) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range("2023-06-01", periods=len(close))
    # Daily range scales with the day's move so swings/ATR are realistic.
    amp = np.abs(np.diff(close, prepend=close[0])) + 0.3
    if vol is None:
        vol = np.full(len(close), 1e6)
    return pd.DataFrame(
        {"open": close, "high": close + amp, "low": close - amp,
         "close": close, "volume": vol},
        index=idx,
    )


def _coil_chart() -> pd.DataFrame:
    """Rising base into a tight, low-volatility consolidation under resistance,
    with volume drying up then a fresh pickup — the canonical coil."""
    n, t = 300, np.arange(300)
    rise = 60 + 0.16 * np.minimum(t, 240)
    osc = np.where(t < 240, 6 * np.cos(t / 5.0), 0.0)        # contracting swings
    flat = np.where(t >= 240, 0.4 * np.sin(t / 3.0), 0.0)    # tight recent range
    vol = np.concatenate([np.full(250, 2e6), np.full(40, 8e5), [2.5e6] * 10])
    return _bars(rise + osc + flat, vol)


def _downtrend_chart() -> pd.DataFrame:
    n, t = 300, np.arange(300)
    return _bars(90 - 0.1 * t + 5 * np.sin(t / 6.0))


# ----- core scoring ---------------------------------------------------------
def test_coil_scores_high_and_is_classified():
    out = evaluate("COIL", _coil_chart())
    assert out is not None
    assert out["setup_score"] >= 60
    assert out["classification"] != CLS_NONE
    # The squeeze and the proximity to overhead resistance are the live signals.
    assert out["factors"]["coil"] >= 0.7
    assert out["factors"]["breakout_proximity"] >= 0.6
    # A tight coil sits low in its own 6-month Bollinger-width range.
    assert out["bbw_pctile"] is not None and out["bbw_pctile"] <= 0.35
    assert any("squeeze" in r.lower() or "tighten" in r.lower() for r in out["reasons"])


def test_coil_provides_trade_geometry():
    out = evaluate("COIL", _coil_chart())
    assert out["breakout_trigger"] is not None
    assert out["stop"] is not None and out["stop"] < out["spot"]


def test_downtrend_scores_below_coil():
    coil = evaluate("COIL", _coil_chart())
    down = evaluate("DOWN", _downtrend_chart())
    assert down is not None
    assert down["setup_score"] < coil["setup_score"]
    assert down["factors"]["coil"] < coil["factors"]["coil"]


def test_overbought_blowoff_is_penalised():
    t = np.arange(300)
    para = evaluate("PARA", _bars(50 * np.exp(0.004 * t)))
    # RSI pinned high -> momentum factor damped (don't chase exhausted moves).
    assert para["rsi"] is not None and para["rsi"] > 72
    assert para["factors"]["momentum"] <= 0.4
    assert any("overbought" in r.lower() for r in para["reasons"])


def test_insufficient_history_returns_none():
    t = np.arange(40)
    assert evaluate("S", _bars(60 + 0.1 * t)) is None


def test_factors_bounded_and_score_consistent():
    out = evaluate("COIL", _coil_chart())
    for v in out["factors"].values():
        assert 0.0 <= v <= 1.0
    assert 0.0 <= out["setup_score"] <= 100.0


# ----- async sweep + job (offline, synthetic provider) ----------------------
def test_scan_universe_ranks_and_is_sorted():
    setups = asyncio.run(scan_universe(["AAA", "BBB", "CCC", "DDD"]))
    assert setups, "synthetic provider should yield scored setups"
    scores = [s["setup_score"] for s in setups]
    assert scores == sorted(scores, reverse=True)


def test_run_screen_without_flow_returns_candidates_shape():
    # include_flow=False avoids any DB dependency; min_score=0 keeps everyone.
    res = asyncio.run(run_screen(top_n=3, min_score=0.0, include_flow=False))
    assert res["scanned"] >= 1
    assert len(res["candidates"]) <= 3
    for c in res["candidates"]:
        assert c["unusual_call_flow"] is False   # flow lookup was skipped
        assert "setup_score" in c and "classification" in c
