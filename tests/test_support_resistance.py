"""Tests for the support/resistance zone engine and the options playbook.

Pure functions over a synthetic OHLCV frame, so these run fully offline.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd

from app.analysis.support_resistance import (
    analyze_levels,
    build_playbook,
    find_levels,
)


def _bars(close: np.ndarray) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    idx = pd.bdate_range("2023-01-01", periods=len(close))
    amp = np.abs(np.diff(close, prepend=close[0])) + 0.2
    return pd.DataFrame(
        {"open": close, "high": close + amp, "low": close - amp,
         "close": close, "volume": np.full(len(close), 1e6)},
        index=idx,
    )


def _ranged() -> pd.DataFrame:
    """Repeatedly oscillates 100<->120, ending mid-range — strong S/R shelves."""
    t = np.arange(260)
    osc = 110 + 10 * np.sin(t / 6.0)
    osc[-1] = 110.0
    return _bars(osc)


# ----- zone detection -------------------------------------------------------
def test_detects_range_support_and_resistance():
    levels = find_levels(_ranged(), spot=110.0, atr=2.0,
                         smas={"sma20": 109, "sma50": 108, "sma200": 105})
    res = [l for l in levels if l["kind"] == "resistance"]
    sup = [l for l in levels if l["kind"] == "support"]
    assert res and sup
    # The repeatedly-tested ceiling ~120 and floor ~100 should be the strongest.
    top_res = max(res, key=lambda l: l["strength"])
    top_sup = max(sup, key=lambda l: l["strength"])
    assert abs(top_res["price"] - 120) <= 2 and top_res["strength"] >= 2
    assert abs(top_sup["price"] - 100) <= 2 and top_sup["strength"] >= 2
    # Heavily-tested levels accumulate touches and confluence tags.
    assert top_res["touches"] >= 3
    assert any("round" in c or "52-week" in c for c in top_res["confluence"])


def test_levels_sorted_nearest_first_with_correct_sign():
    levels = find_levels(_ranged(), spot=110.0, atr=2.0)
    res = [l for l in levels if l["kind"] == "resistance"]
    sup = [l for l in levels if l["kind"] == "support"]
    # Resistance ascends (nearest above first); support descends (nearest below).
    assert res == sorted(res, key=lambda l: l["price"])
    assert sup == sorted(sup, key=lambda l: l["price"], reverse=True)
    assert all(l["price"] > 110 and l["distance_pct"] > 0 for l in res)
    assert all(l["price"] < 110 and l["distance_pct"] < 0 for l in sup)


def test_insufficient_history_returns_empty():
    assert find_levels(_bars(100 + np.arange(10)), spot=105.0) == []


# ----- options playbook -----------------------------------------------------
def test_playbook_has_bull_and_bear_scenarios():
    out = analyze_levels(_ranged(), spot=110.0, atr=2.0,
                         smas={"sma20": 109, "sma50": 108, "sma200": 105})
    pb = out["playbook"]
    sides = {(p["bias"], p["side"]) for p in pb}
    assert ("bullish", "calls") in sides
    assert ("bearish", "puts") in sides
    for p in pb:
        # Every scenario is fully specified and self-consistent.
        assert p["target"] is not None and p["stop"] is not None
        assert p["reward_risk"] is not None and p["reward_risk"] > 0
        assert p["suggested_strike"] > 0


def test_breakout_stop_below_entry_breakdown_stop_above():
    out = analyze_levels(_ranged(), spot=110.0, atr=2.0)
    breakout = next(p for p in out["playbook"]
                    if p["bias"] == "bullish" and "close above" in p["trigger"])
    breakdown = next(p for p in out["playbook"]
                     if p["bias"] == "bearish" and "close below" in p["trigger"])
    # A breakout long stops below the level; the target is above the entry.
    assert breakout["stop"] < breakout["entry_ref"] < breakout["target"]
    # A breakdown short stops above the level; the target is below the entry.
    assert breakdown["stop"] > breakdown["entry_ref"] > breakdown["target"]


def test_analyze_levels_nearest_fields_bracket_spot():
    out = analyze_levels(_ranged(), spot=110.0, atr=2.0)
    assert out["nearest_resistance"] > out["spot"] > out["nearest_support"]


# ----- integration through the technical read (offline/synthetic) -----------
def test_technicals_analyze_includes_levels_and_playbook():
    from app.analysis.technicals import analyze
    out = asyncio.run(analyze("NVDA"))
    assert out["data_source"] != "unavailable"
    assert "sr_levels" in out and "playbook" in out
    assert isinstance(out["sr_levels"], list)
