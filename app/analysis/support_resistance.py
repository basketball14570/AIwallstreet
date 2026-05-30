"""Support / resistance zones + a level-anchored options playbook.

Turns a ticker's daily history into the two things a trader actually needs to
*time* an options entry:

  1. **Ranked S/R zones** — swing pivots clustered into price *zones* (a level is
     rarely one exact price), each scored by how many times and how recently it
     was tested, with confluence tags (a moving average, a round number, the
     52-week extreme sitting on the same shelf makes a level stronger).

  2. **An options playbook** — concrete, level-anchored scenarios: when a break
     above resistance argues for calls, when a hold at support does, when a
     breakdown / rejection argues for puts — each with a trigger, target, stop,
     a rough reward:risk, and a strike to look at. Educational, not advice.

The functions are pure (operate on a daily-OHLCV frame) so they're deterministic
and unit-tested offline. ``analyze_levels`` is the orchestrator the technical
read and the ``/analysis/{ticker}/levels`` endpoint both call.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd

_SWING_WINDOW = 3          # bars each side to qualify a pivot


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _swings(values: list[float], window: int) -> list[tuple[int, float]]:
    """Local maxima as (bar_index, value). Call with negated lows for minima."""
    out: list[tuple[int, float]] = []
    n = len(values)
    for i in range(n):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        if values[i] >= max(values[lo:hi]):
            out.append((i, values[i]))
    return out


def _round_increment(price: float) -> float:
    """The 'psychological round number' spacing at this price magnitude."""
    if price < 5:
        return 0.5
    if price < 20:
        return 1.0
    if price < 100:
        return 5.0
    if price < 500:
        return 10.0
    return 50.0


def _nearest_round(price: float) -> float:
    inc = _round_increment(price)
    return round(round(price / inc) * inc, 2)


def _strike_increment(price: float) -> float:
    """Approximate listed option-strike spacing (varies by name; a sane guess)."""
    if price < 25:
        return 1.0
    if price < 200:
        return 5.0
    return 10.0


def _nearest_strike(price: float) -> float:
    inc = _strike_increment(price)
    return round(round(price / inc) * inc, 2)


@dataclass
class Level:
    price: float            # representative price of the zone
    kind: str               # "support" | "resistance"
    strength: int           # 1..3 (stars)
    touches: int            # pivots that formed the zone
    distance_pct: float     # signed % from current price
    band_low: float         # zone extent
    band_high: float
    confluence: list[str]   # e.g. ["50-day MA", "round $250", "52-week high"]


# --------------------------------------------------------------------------- #
# Zone detection
# --------------------------------------------------------------------------- #
def find_levels(
    bars: pd.DataFrame,
    spot: float,
    atr: float | None = None,
    smas: dict[str, float | None] | None = None,
    max_per_side: int = 4,
) -> list[dict]:
    """Cluster swing pivots into ranked support/resistance zones."""
    if bars is None or bars.empty or len(bars) < 20 or spot <= 0:
        return []
    highs = bars["high"].astype(float).tolist()
    lows = bars["low"].astype(float).tolist()
    n = len(highs)
    tol = max((atr or 0) * 0.5, spot * 0.0075)   # zone width: ½ ATR or 0.75%

    # Pivots carry a recency weight (newer touches matter more).
    pivots: list[tuple[float, float]] = []   # (price, weight)
    for idx, val in _swings(highs, _SWING_WINDOW):
        pivots.append((val, 0.5 + 0.5 * (idx / n)))
    for idx, val in (( i, -v) for i, v in _swings([-x for x in lows], _SWING_WINDOW)):
        pivots.append((val, 0.5 + 0.5 * (idx / n)))
    if not pivots:
        return []

    # Greedy 1-D clustering of nearby pivots into zones.
    pivots.sort(key=lambda p: p[0])
    clusters: list[list[tuple[float, float]]] = [[pivots[0]]]
    for price, w in pivots[1:]:
        if price - clusters[-1][-1][0] <= tol:
            clusters[-1].append((price, w))
        else:
            clusters.append([(price, w)])

    smas = smas or {}
    levels: list[Level] = []
    for cl in clusters:
        wsum = sum(w for _, w in cl) or 1.0
        price = round(sum(p * w for p, w in cl) / wsum, 2)
        if abs(price - spot) / spot < 0.002:    # straddling price — not actionable
            continue
        kind = "resistance" if price > spot else "support"
        touches = len(cl)
        recency = sum(w for _, w in cl) / touches      # 0.5..1.0
        band_low = round(min(p for p, _ in cl), 2)
        band_high = round(max(p for p, _ in cl), 2)

        confluence: list[str] = []
        for label, ma in (("20-day MA", smas.get("sma20")),
                          ("50-day MA", smas.get("sma50")),
                          ("200-day MA", smas.get("sma200"))):
            if ma and abs(ma - price) <= tol:
                confluence.append(label)
        rnd = _nearest_round(price)
        if abs(rnd - price) <= tol:
            confluence.append(f"round ${rnd:g}")

        # Strength: more touches + more recent + confluence => stronger.
        score = touches + (recency - 0.5) * 2 + len(confluence)
        strength = 3 if score >= 4 else 2 if score >= 2.2 else 1
        levels.append(Level(
            price=price, kind=kind, strength=strength, touches=touches,
            distance_pct=round((price - spot) / spot * 100, 2),
            band_low=band_low, band_high=band_high, confluence=confluence,
        ))

    # Fold in the 52-week extremes as their own strong shelves if not covered.
    window_52w = bars.tail(252)
    hi52 = round(float(window_52w["high"].max()), 2)
    lo52 = round(float(window_52w["low"].min()), 2)
    for price, kind, tag in ((hi52, "resistance", "52-week high"),
                             (lo52, "support", "52-week low")):
        if price <= 0 or abs(price - spot) / spot < 0.002:
            continue
        if (kind == "resistance" and price <= spot) or (kind == "support" and price >= spot):
            continue
        existing = next((l for l in levels if abs(l.price - price) <= tol), None)
        if existing:
            if tag not in existing.confluence:
                existing.confluence.append(tag)
                existing.strength = min(3, existing.strength + 1)
        else:
            levels.append(Level(
                price=price, kind=kind, strength=2, touches=1,
                distance_pct=round((price - spot) / spot * 100, 2),
                band_low=price, band_high=price, confluence=[tag],
            ))

    res = sorted((l for l in levels if l.kind == "resistance"), key=lambda l: l.price)
    sup = sorted((l for l in levels if l.kind == "support"), key=lambda l: l.price, reverse=True)
    return [asdict(l) for l in (res[:max_per_side] + sup[:max_per_side])]


# --------------------------------------------------------------------------- #
# Options playbook
# --------------------------------------------------------------------------- #
def _reachability(distance: float, atr: float | None) -> str:
    if not atr or atr <= 0:
        return ""
    days = abs(distance) / atr
    if days <= 2:
        return f"~{days:.1f} typical daily moves away — reachable quickly"
    if days <= 6:
        return f"~{days:.0f} typical daily moves away — reasonable swing"
    return f"~{days:.0f} typical daily moves away — a stretch; needs a real catalyst"


def _scenario(bias: str, side: str, trigger: str, entry: float, target: float | None,
              stop: float | None, atr: float | None, note: str) -> dict:
    rr = None
    if target is not None and stop is not None and abs(entry - stop) > 1e-9:
        rr = round(abs(target - entry) / abs(entry - stop), 2)
    strike = _nearest_strike(entry if side == "calls"
                             else entry)  # ATM-ish at the trigger
    return {
        "bias": bias, "side": side, "trigger": trigger,
        "entry_ref": round(entry, 2),
        "target": round(target, 2) if target is not None else None,
        "stop": round(stop, 2) if stop is not None else None,
        "reward_risk": rr,
        "suggested_strike": strike,
        "reachability": _reachability((target - entry) if target else 0, atr),
        "note": note,
    }


def build_playbook(spot: float, levels: list[dict], atr: float | None,
                   trend: str | None = None) -> list[dict]:
    """Concrete call/put scenarios anchored to the nearest S/R zones.

    Breakout / breakdown entries stop on a *failed move* (a tight buffer back
    inside the level), not at the far next level — that's the real trade and it
    keeps reward:risk honest. Bounce / rejection entries stop just beyond the
    level being defended.
    """
    res = [l for l in levels if l["kind"] == "resistance"]   # already nearest-first
    sup = [l for l in levels if l["kind"] == "support"]
    r1 = res[0] if res else None
    r2 = res[1] if len(res) > 1 else None
    s1 = sup[0] if sup else None
    s2 = sup[1] if len(sup) > 1 else None
    # Failed-move buffer: ~⅓ of a typical day, or 0.4% of price.
    buf = max((atr or 0) * 0.35, spot * 0.004)
    plays: list[dict] = []

    # 1. Bullish breakout — calls on a confirmed break above the first ceiling.
    if r1:
        trig = r1["price"]
        target = (r2["price"] if r2
                  else round(trig + (trig - s1["price"]), 2) if s1
                  else round(trig + 3 * atr, 2) if atr else None)
        stop = round(r1["band_low"] - buf, 2)   # back below the level = failed
        plays.append(_scenario(
            "bullish", "calls",
            f"a daily close above resistance ${trig:,.2f}", trig, target, stop, atr,
            "Buy calls only after it clears and holds the level — a break that "
            "fails back below is the classic bull trap. Stop on a close back inside."))

    # 2. Bullish bounce — calls if the first support holds and turns price up.
    if s1 and r1:
        entry = s1["price"]
        stop = round(s1["band_low"] - buf, 2)
        plays.append(_scenario(
            "bullish", "calls",
            f"price tests support ${entry:,.2f} and holds (a bounce/reversal bar)",
            entry, r1["price"], stop, atr,
            "Lower-risk long entry: buy the bounce off support with a tight stop "
            "just below it, targeting the first resistance overhead."))

    # 3. Bearish breakdown — puts on a confirmed break below the first floor.
    if s1:
        trig = s1["price"]
        target = (s2["price"] if s2
                  else round(trig - (r1["price"] - trig), 2) if r1
                  else round(trig - 3 * atr, 2) if atr else None)
        stop = round(s1["band_high"] + buf, 2)   # back above the level = failed
        plays.append(_scenario(
            "bearish", "puts",
            f"a daily close below support ${trig:,.2f}", trig, target, stop, atr,
            "Buy puts only on a decisive break and hold below the floor; a quick "
            "reclaim is a failed breakdown. Stop on a close back inside."))

    # 4. Bearish rejection — puts if the first resistance rejects price.
    if r1 and s1:
        entry = r1["price"]
        stop = round(r1["band_high"] + buf, 2)
        plays.append(_scenario(
            "bearish", "puts",
            f"price tests resistance ${entry:,.2f} and rejects (a reversal bar)",
            entry, s1["price"], stop, atr,
            "Fade strength into the ceiling with puts, stop just above it, "
            "targeting the first support below."))

    return plays


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def analyze_levels(bars: pd.DataFrame, spot: float, atr: float | None = None,
                   smas: dict[str, float | None] | None = None,
                   trend: str | None = None) -> dict:
    """Ranked S/R zones + the level-anchored options playbook for one ticker."""
    levels = find_levels(bars, spot, atr=atr, smas=smas)
    playbook = build_playbook(spot, levels, atr, trend=trend)
    nearest_res = next((l for l in levels if l["kind"] == "resistance"), None)
    nearest_sup = next((l for l in levels if l["kind"] == "support"), None)
    return {
        "spot": round(spot, 2),
        "sr_levels": levels,
        "playbook": playbook,
        "nearest_resistance": nearest_res["price"] if nearest_res else None,
        "nearest_support": nearest_sup["price"] if nearest_sup else None,
    }
