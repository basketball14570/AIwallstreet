"""Self-driven technical breakout screener.

The options-flow engine is *reactive*: it scores prints that arrive. This module
is *proactive* — it sweeps a universe of stocks and, from price/volume alone,
finds names that are mechanically coiling for an expansion move ("about to
explode"), with no options flow required.

The thesis is the classic pre-breakout footprint:

  * a **volatility squeeze** — Bollinger Band width pinched to a multi-month low
    (a coil that historically precedes a violent range expansion);
  * **volume dry-up then pickup** — supply exhausts on quiet tape, then the
    first sign of demand returns;
  * **breakout proximity** — price pressed just under an overhead ceiling
    (swing-high resistance / 52-week high), where a break triggers momentum;
  * **constructive trend** — above a rising 50-day average (a base, not a
    falling knife);
  * **momentum with room** — RSI mid-range and MACD turning up, not an
    exhausted blow-off;
  * **relative strength** — holding gains over the trailing weeks.

Each factor is normalised to ``[0,1]``, combined with tunable weights into a
``setup_score`` (0-100), and classified. Every score carries human-readable
``reasons`` — the same "explain WHY" contract the flow engine honours.

The scoring is a **pure function** over a daily-OHLCV DataFrame so it is
deterministic and unit-testable offline; ``scan_ticker`` / ``scan_universe`` are
thin async wrappers that fetch bars and rank.
"""
from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.core.logging import get_logger
from app.providers.prices import PriceHistoryProvider

log = get_logger("analysis.screener")

_provider = PriceHistoryProvider()
_LOOKBACK_DAYS = 400          # ~14 months of sessions for stable baselines
_SWING_WINDOW = 3             # bars each side to qualify a local high/low
_MAX_CONCURRENCY = 8          # bound fan-out so we don't burst the price API

# Composite weights (sum to 1.0). Tunable; the coil and breakout-proximity carry
# the most signal for an imminent expansion.
_WEIGHTS = {
    "coil": 0.28,
    "breakout_proximity": 0.22,
    "volume": 0.15,
    "trend": 0.15,
    "momentum": 0.12,
    "rel_strength": 0.08,
}

# Classification bands on the 0-100 composite.
CLS_IMMINENT = "Breakout Imminent"
CLS_COILING = "Coiling Tightly"
CLS_LEADER = "Momentum Leader"
CLS_BASING = "Building a Base"
CLS_NONE = "No Setup"


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _rsi(close: pd.Series, period: int = 14) -> float | None:
    if len(close) < period + 1:
        return None
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    last_gain, last_loss = avg_gain.iloc[-1], avg_loss.iloc[-1]
    if last_loss == 0:
        return 100.0
    rs = last_gain / last_loss
    return round(float(100 - 100 / (1 + rs)), 1)


def _macd_hist(close: pd.Series) -> tuple[float, float] | None:
    """Returns (latest_hist, prev_hist) so we can tell if momentum is turning up."""
    if len(close) < 35:
        return None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    return round(float(hist.iloc[-1]), 4), round(float(hist.iloc[-2]), 4)


def _sma(close: pd.Series, n: int) -> float | None:
    return round(float(close.tail(n).mean()), 4) if len(close) >= n else None


def _bbw_series(close: pd.Series, n: int = 20, k: float = 2.0) -> pd.Series:
    """Bollinger Band Width = (upper-lower)/mid, as a fraction. Lower = tighter."""
    mid = close.rolling(n).mean()
    std = close.rolling(n).std(ddof=0)
    return (2 * k * std) / mid


def _swings(values: list[float], window: int) -> list[float]:
    """Local maxima: a bar that is the max within +/- ``window`` bars. Call with
    negated lows to get local minima."""
    out: list[float] = []
    n = len(values)
    for i in range(n):
        lo = max(0, i - window)
        hi = min(n, i + window + 1)
        if values[i] >= max(values[lo:hi]):
            out.append(values[i])
    return out


@dataclass
class BreakoutSetup:
    ticker: str
    spot: float
    data_source: str
    setup_score: float                 # 0-100 composite
    classification: str
    factors: dict[str, float]          # per-factor sub-scores in [0,1]
    breakout_trigger: float | None     # break above -> momentum
    stop: float | None                 # nearest support to fail the thesis
    target: float | None               # measured-move objective
    # Supporting readings (for the dashboard / transparency).
    bbw: float | None                  # current Bollinger band width (fraction)
    bbw_pctile: float | None           # where current BBW sits in its 6-month range
    rsi: float | None
    macd_hist: float | None
    sma20: float | None
    sma50: float | None
    atr_pct: float | None              # ATR(14) as % of price
    chg_1m: float | None
    chg_3m: float | None
    rel_volume: float | None           # latest day vs 20-day average
    pct_to_52w_high: float | None
    reasons: list[str]


def evaluate(ticker: str, bars: pd.DataFrame, source: str = "polygon") -> dict | None:
    """Score a single ticker's daily OHLCV history. Pure function — no I/O.

    Returns a ``BreakoutSetup`` as a dict, or ``None`` when there isn't enough
    history to say anything meaningful.
    """
    ticker = ticker.upper()
    if bars is None or bars.empty or len(bars) < 60:
        return None
    close = bars["close"].astype(float)
    high = bars["high"].astype(float)
    low = bars["low"].astype(float)
    spot = round(float(close.iloc[-1]), 2)
    if spot <= 0:
        return None

    reasons: list[str] = []
    factors: dict[str, float] = {}

    # ---- 1. Coil (Bollinger squeeze) -------------------------------------
    bbw = _bbw_series(close)
    cur_bbw = float(bbw.iloc[-1]) if not np.isnan(bbw.iloc[-1]) else None
    bbw_pctile: float | None = None
    coil = 0.0
    if cur_bbw is not None:
        window = bbw.tail(126).dropna()           # ~6 months
        if len(window) >= 30:
            bbw_pctile = round(float((window < cur_bbw).mean()), 3)
            # Tightest in its own range -> strongest coil. Low percentile = tight.
            coil = _clamp(1.0 - bbw_pctile)
            # Reward an active *contraction* (tighter than 20 sessions ago).
            past = float(bbw.iloc[-21]) if len(bbw) > 21 and not np.isnan(bbw.iloc[-21]) else None
            if past and cur_bbw < past:
                coil = _clamp(coil + 0.10)
            if bbw_pctile <= 0.15:
                reasons.append(
                    f"Volatility squeeze — Bollinger width at the {bbw_pctile:.0%} "
                    "of its 6-month range (a tight coil that precedes expansion)")
            elif bbw_pctile <= 0.35:
                reasons.append(
                    f"Range tightening — Bollinger width in the lower "
                    f"{bbw_pctile:.0%} of its 6-month range")
    factors["coil"] = round(coil, 3)

    # ---- 2. Breakout proximity -------------------------------------------
    swing_highs = sorted({round(v, 2) for v in _swings(high.tolist(), _SWING_WINDOW)})
    swing_lows = sorted({round(-v, 2) for v in _swings([-x for x in low.tolist()], _SWING_WINDOW)})
    resistance = min((h for h in swing_highs if h > spot * 1.001), default=None)
    support = max((lo for lo in swing_lows if lo < spot * 0.999), default=None)
    window_52w = bars.tail(252)
    high_52w = round(float(window_52w["high"].max()), 2)
    low_52w = round(float(window_52w["low"].min()), 2)
    pct_to_52w_high = round((high_52w - spot) / spot * 100, 2) if high_52w else None

    prox = 0.0
    if resistance is not None:
        dist = (resistance - spot) / spot
        # Pressed within ~6% under a ceiling scores high; fades out by ~15%.
        prox = _clamp(1.0 - dist / 0.15)
        if dist <= 0.04:
            reasons.append(
                f"Coiled just under resistance ${resistance:,.2f} "
                f"(+{dist:.1%}) — a break triggers momentum")
    else:
        # No overhead swing high => already at/near range highs (breakout/blue sky).
        prox = 0.85
        reasons.append("At the top of its range — no overhead resistance (blue-sky breakout zone)")
    # A name within a few percent of its 52-week high is primed.
    if pct_to_52w_high is not None and 0 <= pct_to_52w_high <= 5:
        prox = _clamp(prox + 0.10)
        reasons.append(f"Within {pct_to_52w_high:.1f}% of its 52-week high")
    factors["breakout_proximity"] = round(prox, 3)

    # ---- 3. Volume: dry-up then pickup -----------------------------------
    vol_score = 0.5
    rel_volume: float | None = None
    if "volume" in bars and (bars["volume"] > 0).any():
        vol = bars["volume"].astype(float)
        avg10 = float(vol.tail(10).mean())
        avg50 = float(vol.tail(50).mean()) if len(vol) >= 50 else float(vol.mean())
        avg20 = float(vol.tail(20).mean())
        latest = float(vol.iloc[-1])
        rel_volume = round(latest / avg20, 2) if avg20 > 0 else None
        dryup = _clamp((1.0 - avg10 / avg50) / 0.5) if avg50 > 0 else 0.0  # quiet base
        pickup = _clamp((rel_volume - 1.0) / 1.0) if rel_volume else 0.0    # demand returns
        # Best case: a quiet base (dry-up) with a fresh pickup on the latest bar.
        vol_score = _clamp(0.4 + 0.4 * dryup + 0.3 * pickup)
        if dryup > 0.4 and pickup > 0.3:
            reasons.append(
                f"Volume dried up then turned ({rel_volume}x its 20-day avg today) "
                "— supply exhausted, demand returning")
        elif rel_volume and rel_volume >= 2.0:
            reasons.append(f"Volume {rel_volume}x its 20-day average — unusually active")
    factors["volume"] = round(vol_score, 3)

    # ---- 4. Trend (constructive base, not a falling knife) ---------------
    sma20, sma50 = _sma(close, 20), _sma(close, 50)
    sma50_rising = (len(close) >= 60
                    and float(close.tail(50).mean()) > float(close.iloc[-60:-10].mean()))
    trend = 0.0
    if sma50 is not None:
        if spot > sma50:
            trend += 0.5
        if sma20 is not None and sma20 > sma50:
            trend += 0.25
        if sma50_rising:
            trend += 0.25
        if trend >= 0.75:
            reasons.append("Constructive trend — above a rising 50-day average")
    factors["trend"] = round(_clamp(trend), 3)

    # ---- 5. Momentum (room to run, turning up) ---------------------------
    rsi = _rsi(close)
    mh = _macd_hist(close)
    macd_hist = mh[0] if mh else None
    momentum = 0.5
    if rsi is not None:
        if 50 <= rsi <= 65:
            momentum = 0.9          # ideal: strong but not overbought
        elif 45 <= rsi < 50:
            momentum = 0.7
        elif 65 < rsi <= 72:
            momentum = 0.6
        elif rsi > 72:
            momentum = 0.25         # overbought / exhausted
        elif 38 <= rsi < 45:
            momentum = 0.45
        else:
            momentum = 0.25         # downtrend
    if mh is not None:
        if mh[0] > 0 and mh[0] >= mh[1]:
            momentum = _clamp(momentum + 0.1)
            reasons.append("MACD momentum building (histogram turning up)")
        elif mh[0] < mh[1]:
            momentum = _clamp(momentum - 0.1)
    if rsi is not None and rsi > 72:
        reasons.append(f"RSI {rsi} — overbought; entries are extended")
    factors["momentum"] = round(_clamp(momentum), 3)

    # ---- 6. Relative strength (holding gains) ----------------------------
    chg_1m = round(float(close.iloc[-1] / close.iloc[-22] - 1) * 100, 2) if len(close) > 22 else None
    chg_3m = round(float(close.iloc[-1] / close.iloc[-64] - 1) * 100, 2) if len(close) > 64 else None
    rs = 0.5
    if chg_1m is not None:
        # Reward modest-to-strong positive momentum; parabolic (>60%) is risky.
        if 5 <= chg_1m <= 40:
            rs = 0.9
        elif 0 <= chg_1m < 5:
            rs = 0.6
        elif 40 < chg_1m <= 60:
            rs = 0.7
        elif chg_1m > 60:
            rs = 0.5            # already extended
        else:
            rs = _clamp(0.5 + chg_1m / 40)   # negative -> below 0.5
    factors["rel_strength"] = round(_clamp(rs), 3)

    # ---- Composite + classification --------------------------------------
    score = sum(_WEIGHTS[k] * factors[k] for k in _WEIGHTS)
    setup_score = round(score * 100, 1)

    if setup_score >= 75 and factors["breakout_proximity"] >= 0.6 and factors["coil"] >= 0.5:
        classification = CLS_IMMINENT
    elif factors["coil"] >= 0.7 and setup_score >= 60:
        classification = CLS_COILING
    elif factors["trend"] >= 0.75 and factors["momentum"] >= 0.7 and setup_score >= 60:
        classification = CLS_LEADER
    elif setup_score >= 50:
        classification = CLS_BASING
    else:
        classification = CLS_NONE

    # ---- Trade geometry ---------------------------------------------------
    breakout_trigger = resistance if resistance is not None else high_52w
    stop = support if support is not None else low_52w
    atr = _atr(bars)
    atr_pct = round(atr / spot * 100, 2) if atr and spot else None
    target = None
    if breakout_trigger:
        # Measured move: project the recent base height above the trigger; fall
        # back to the next swing high if there is one.
        higher = [h for h in swing_highs if h > breakout_trigger * 1.001]
        if higher:
            target = higher[0]
        elif support:
            target = round(breakout_trigger + (breakout_trigger - support), 2)
        elif atr:
            target = round(breakout_trigger + 3 * atr, 2)

    if not reasons:
        reasons.append("No standout technical setup right now.")

    setup = BreakoutSetup(
        ticker=ticker, spot=spot, data_source=source,
        setup_score=setup_score, classification=classification, factors=factors,
        breakout_trigger=breakout_trigger, stop=stop, target=target,
        bbw=round(cur_bbw, 4) if cur_bbw is not None else None,
        bbw_pctile=bbw_pctile, rsi=rsi, macd_hist=macd_hist,
        sma20=round(sma20, 2) if sma20 else None,
        sma50=round(sma50, 2) if sma50 else None,
        atr_pct=atr_pct, chg_1m=chg_1m, chg_3m=chg_3m, rel_volume=rel_volume,
        pct_to_52w_high=pct_to_52w_high, reasons=reasons,
    )
    return asdict(setup)


def _atr(bars: pd.DataFrame, period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    h, l, prev_c = bars["high"], bars["low"], bars["close"].shift(1)
    tr = pd.concat([h - l, (h - prev_c).abs(), (l - prev_c).abs()], axis=1).max(axis=1)
    return round(float(tr.tail(period).mean()), 4)


async def scan_ticker(ticker: str) -> dict | None:
    """Fetch a ticker's history and score it. Returns None on data/score miss."""
    from app.config import settings

    ticker = ticker.upper()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)
    try:
        bars = await _provider.daily_bars(ticker, start, end)
    except Exception as exc:  # noqa: BLE001 — one bad ticker can't stop a scan
        log.warning("screener fetch failed", ticker=ticker, error=str(exc))
        return None
    source = "polygon" if settings.polygon_api_key else "synthetic"
    return evaluate(ticker, bars, source=source)


async def scan_universe(tickers: list[str]) -> list[dict]:
    """Score every ticker concurrently and return setups ranked best-first."""
    sem = asyncio.Semaphore(_MAX_CONCURRENCY)

    async def _one(t: str) -> dict | None:
        async with sem:
            return await scan_ticker(t)

    results = await asyncio.gather(*(_one(t) for t in tickers), return_exceptions=True)
    setups: list[dict] = []
    for r in results:
        if isinstance(r, dict):
            setups.append(r)
        elif isinstance(r, Exception):
            log.warning("screener ticker errored", error=str(r))
    setups.sort(key=lambda s: s["setup_score"], reverse=True)
    return setups
