"""Price-level analysis for alerts.

Turns a ticker's recent daily chart into the levels a non-analyst cares about:
where it might run to (overhead resistance / the strike the flow is targeting)
and where it might fall back to (support). Everything is derived from real
Polygon bars — no guessing. Results are cached per ticker for a few minutes so
a burst of alerts on one name doesn't hammer the API.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.core.logging import get_logger
from app.providers.prices import PriceHistoryProvider
from app.schemas.flow import ContractType, FlowEvent

log = get_logger("analysis.levels")

_provider = PriceHistoryProvider()
_cache: dict[str, tuple[float, "ChartLevels | None"]] = {}
_TTL_SEC = 600          # 10 minutes
_LOOKBACK_DAYS = 150    # ~7 months of trading sessions
_SWING_WINDOW = 3       # bars on each side to qualify a local high/low


@dataclass
class ChartLevels:
    swing_highs: list[float]   # local peaks, ascending
    swing_lows: list[float]    # local troughs, ascending
    range_high: float
    range_low: float
    ma20: float | None


def _swings(values: list[float], window: int) -> list[float]:
    """Local extrema: a bar that is the max (or min, depending on sign) within
    +/- ``window`` bars. Called once for highs and once for negated lows."""
    out: list[float] = []
    n = len(values)
    for i in range(n):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        if values[i] >= max(values[lo:hi]):
            out.append(values[i])
    return out


async def _compute(ticker: str) -> ChartLevels | None:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)
    bars = await _provider.daily_bars(ticker, start, end)
    if bars.empty or len(bars) < 10:
        return None
    highs = bars["high"].tolist()
    lows = bars["low"].tolist()
    swing_highs = sorted({round(v, 2) for v in _swings(highs, _SWING_WINDOW)})
    swing_lows = sorted({round(-v, 2) for v in _swings([-x for x in lows], _SWING_WINDOW)})
    ma20 = round(float(bars["close"].tail(20).mean()), 2) if len(bars) >= 20 else None
    return ChartLevels(
        swing_highs=swing_highs, swing_lows=swing_lows,
        range_high=round(float(bars["high"].max()), 2),
        range_low=round(float(bars["low"].min()), 2),
        ma20=ma20,
    )


async def chart_levels(ticker: str) -> ChartLevels | None:
    """Cached per-ticker chart levels."""
    ticker = ticker.upper()
    now = time.time()
    hit = _cache.get(ticker)
    if hit and now - hit[0] < _TTL_SEC:
        return hit[1]
    levels = await _compute(ticker)
    _cache[ticker] = (now, levels)
    return levels


def _pct(target: float, spot: float) -> str:
    return f"{(target - spot) / spot:+.1%}"


async def levels_block(event: FlowEvent) -> str:
    """Formatted multi-line levels summary for an alert, or '' if unavailable."""
    spot = event.spot
    if not spot or spot <= 0:
        return ""
    cl = await chart_levels(event.ticker)
    if cl is None:
        return ""

    # Nearest level above/below the current price.
    resistance = min((h for h in cl.swing_highs if h > spot * 1.002), default=None)
    support = max((lo for lo in cl.swing_lows if lo < spot * 0.998), default=None)
    is_call = event.contract_type == ContractType.CALL

    lines = [f"Levels — {event.ticker}  (spot ${spot:,.2f})"]
    lines.append(
        f"  Flow target (strike): ${event.strike:,.2f}  ({_pct(event.strike, spot)})"
    )
    up = f"  Up  → resistance ${resistance:,.2f}" if resistance else "  Up  → no overhead swing high"
    up += f"  ·  range high ${cl.range_high:,.2f}"
    dn = f"  Down→ support ${support:,.2f}" if support else "  Down→ no nearby swing low"
    dn += f"  ·  range low ${cl.range_low:,.2f}"
    # Lead with the side the flow is leaning.
    lines += ([up, dn] if is_call else [dn, up])
    if cl.ma20 is not None:
        trend = "above" if spot >= cl.ma20 else "below"
        lines.append(f"  20-day avg ${cl.ma20:,.2f} (price {trend} trend)")
    return "\n".join(lines)
