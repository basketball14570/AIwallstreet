"""Smart Money Score — a ticker-level rollup that ranks where institutional-
looking activity is concentrating *right now*.

Why a separate score? The per-event scoring engine answers "how unusual is
THIS print?" — perfect for alerts. A trader who wakes up and asks "where
should I even look today?" needs the opposite: one number per ticker so
the watchlist can be sorted by where smart-money-style activity is piling up.

The score is a weighted blend of six axes, each normalised to [0, 1]:

  bought_premium      real aggressive call/put buying (lifting the offer)
  vol_oi_unusualness  max vol/OI across the ticker's contracts in the window
  opening_share       fraction of prints flagged opening (new positions)
  iv_discount         1 - IV-rank (institutions tend to buy cheap volatility)
  conviction          per-event confidence, weighted by premium
  breadth             distinct contracts traded (thesis, not random tape)

Output: a 0-100 SMS, a bullish tilt %, a component breakdown, and 2-4 reason
strings the UI can show in a "why" column.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


WEIGHTS: dict[str, float] = {
    "bought_premium":     0.30,
    "vol_oi_unusualness": 0.20,
    "opening_share":      0.15,
    "iv_discount":        0.10,
    "conviction":         0.15,
    "breadth":            0.10,
}


@dataclass
class TickerStats:
    """Aggregated stats for one ticker over the lookback window. Populated
    from a single SQL group-by query; passed unchanged into the scorer."""
    bought_premium: float = 0.0       # $ premium hitting the ask
    sold_premium: float = 0.0         # $ premium hitting the bid
    total_premium: float = 0.0
    max_vol_oi: float | None = None   # max vol/OI ratio across the ticker's contracts
    opening_prints: int = 0
    total_prints: int = 0
    avg_iv_rank: float | None = None  # 0..1
    weighted_confidence: float = 0.0  # sum(conf * premium) / sum(premium), 0..100
    distinct_contracts: int = 0
    call_premium: float = 0.0
    put_premium: float = 0.0


def _logistic(x: float, x0: float, k: float) -> float:
    try:
        return 1.0 / (1.0 + math.exp(-k * (x - x0)))
    except OverflowError:
        return 0.0 if x < x0 else 1.0


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_smart_money(s: TickerStats) -> dict:
    """Return SMS components + composite. Pure function — no I/O."""
    # Bought premium: $500k inflection, $2-3M saturates the signal.
    bought = _logistic(s.bought_premium, x0=500_000, k=1.0 / 750_000)

    # Vol/OI: 0.5 contributes ~0, 2x ~0.16, 5x ~0.47, 10x ~1.0.
    vo = s.max_vol_oi or 0.0
    vol_oi = _clamp01((vo - 0.5) / 9.5) if vo > 0 else 0.0

    # Opening share: fraction of prints flagged opening; require N>=3 to count.
    opening = (s.opening_prints / s.total_prints
               if s.total_prints >= 3 else 0.0)
    opening = _clamp01(opening)

    # IV discount: prefer cheap vol. Unknown -> neutral 0.5 -> contributes 0.5.
    iv_rank = s.avg_iv_rank if s.avg_iv_rank is not None else 0.5
    iv_discount = _clamp01(1.0 - iv_rank)

    # Conviction: per-premium-weighted event confidence (0-100) → 0-1.
    conviction = _clamp01(s.weighted_confidence / 100.0)

    # Breadth: distinct contracts. 1 → 0, 5 → ~0.44, 10+ → 1.
    breadth = _clamp01((s.distinct_contracts - 1) / 9.0)

    components = {
        "bought_premium":     round(bought, 3),
        "vol_oi_unusualness": round(vol_oi, 3),
        "opening_share":      round(opening, 3),
        "iv_discount":        round(iv_discount, 3),
        "conviction":         round(conviction, 3),
        "breadth":            round(breadth, 3),
    }
    composite = sum(WEIGHTS[k] * v for k, v in components.items())
    score = round(100.0 * _clamp01(composite), 1)

    # Bullish tilt: % of *directional* (call + put) premium that is on calls.
    # Unknown -> 50%. This is the lean, *not* a confidence in direction.
    directional = s.call_premium + s.put_premium
    bull_tilt = (100.0 * s.call_premium / directional
                 if directional > 0 else 50.0)

    return {
        "smart_money_score": score,
        "bullish_tilt": round(bull_tilt, 1),
        "components": components,
        "reasons": _explain(s, components),
    }


def _explain(s: TickerStats, c: dict) -> list[str]:
    """2-4 plain reasons the score is what it is. Drives the UI 'why' column."""
    out: list[str] = []
    bp = s.bought_premium
    if c["bought_premium"] >= 0.6:
        out.append(f"${bp/1e6:.1f}M aggressive buy premium" if bp >= 1e6
                   else f"${bp/1e3:.0f}K aggressive buy premium")
    elif c["bought_premium"] >= 0.3:
        out.append(f"${bp/1e6:.1f}M buy premium" if bp >= 1e6
                   else f"${bp/1e3:.0f}K buy premium")
    if c["vol_oi_unusualness"] >= 0.5 and s.max_vol_oi:
        out.append(f"{s.max_vol_oi:.1f}x vol/OI on top contract")
    if c["opening_share"] >= 0.6:
        out.append(f"{int(c['opening_share']*100)}% opening prints")
    if c["iv_discount"] >= 0.6:
        out.append("buying cheap volatility (low IV-rank)")
    if c["breadth"] >= 0.6:
        out.append(f"{s.distinct_contracts} contracts traded")
    if c["conviction"] >= 0.7:
        out.append("high per-event conviction")
    return out[:4]
