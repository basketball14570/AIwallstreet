"""On-demand technical read for a single ticker.

Turns a ticker's daily history into a novice-friendly summary: trend vs moving
averages, momentum (RSI / MACD), support & resistance, and suggested price-alert
levels (breakout above / breakdown below). All grounded in real Polygon daily
bars — this is mechanical technical analysis, not a prediction.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from app.analysis.levels import _swings, _SWING_WINDOW
from app.core.logging import get_logger
from app.providers.prices import PriceHistoryProvider

log = get_logger("analysis.technicals")

_provider = PriceHistoryProvider()
_LOOKBACK_DAYS = 730   # ~2y so the 200-day average is meaningful


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
    return round(100 - 100 / (1 + rs), 1)


def _macd(close: pd.Series) -> tuple[float, float, float] | None:
    if len(close) < 35:
        return None
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    signal = macd.ewm(span=9, adjust=False).mean()
    hist = macd - signal
    return round(float(macd.iloc[-1]), 4), round(float(signal.iloc[-1]), 4), round(float(hist.iloc[-1]), 4)


def _sma(close: pd.Series, n: int) -> float | None:
    return round(float(close.tail(n).mean()), 2) if len(close) >= n else None


@dataclass
class TechnicalRead:
    ticker: str
    spot: float
    data_source: str
    trend: str
    sma20: float | None
    sma50: float | None
    sma200: float | None
    rsi: float | None
    rsi_state: str
    macd: float | None
    macd_signal: float | None
    macd_hist: float | None
    macd_state: str
    supports: list[float]
    resistances: list[float]
    range_high: float
    range_low: float
    breakout_above: float | None
    breakdown_below: float | None
    upside_target: float | None
    notes: list[str]


async def analyze(ticker: str) -> dict:
    ticker = ticker.upper()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=_LOOKBACK_DAYS)
    try:
        bars = await _provider.daily_bars(ticker, start, end)
    except Exception as exc:  # noqa: BLE001
        log.warning("technicals fetch failed", ticker=ticker, error=str(exc))
        return {"ticker": ticker, "data_source": "unavailable",
                "error": "Price history isn't available for this ticker on your "
                         "data plan (Polygon stock aggregates). The flow signals "
                         "still work; chart levels need stock history."}
    if bars.empty or len(bars) < 20:
        return {"ticker": ticker, "data_source": "unavailable",
                "error": "Not enough price history returned for this ticker."}

    from app.config import settings
    source = "polygon" if settings.polygon_api_key else "synthetic"
    close = bars["close"]
    spot = round(float(close.iloc[-1]), 2)

    sma20, sma50, sma200 = _sma(close, 20), _sma(close, 50), _sma(close, 200)
    rsi = _rsi(close)
    macd_t = _macd(close)
    macd, macd_signal, macd_hist = macd_t if macd_t else (None, None, None)

    # Swing-based levels over the window.
    swing_highs = sorted({round(v, 2) for v in _swings(bars["high"].tolist(), _SWING_WINDOW)})
    swing_lows = sorted({round(-v, 2) for v in _swings([-x for x in bars["low"].tolist()], _SWING_WINDOW)})
    resistances = [h for h in swing_highs if h > spot * 1.002][:3]
    supports = sorted([lo for lo in swing_lows if lo < spot * 0.998], reverse=True)[:3]
    range_high = round(float(bars["high"].max()), 2)
    range_low = round(float(bars["low"].min()), 2)

    breakout_above = resistances[0] if resistances else range_high
    breakdown_below = supports[0] if supports else range_low
    upside_target = resistances[1] if len(resistances) > 1 else range_high

    # Trend read.
    if sma50 and sma200:
        if spot > sma50 > sma200:
            trend = "Uptrend — price above the 50 & 200-day averages (bullish stack)"
        elif spot < sma50 < sma200:
            trend = "Downtrend — price below the 50 & 200-day averages (bearish stack)"
        elif spot > sma200:
            trend = "Choppy but holding above the 200-day average (longer-term up)"
        else:
            trend = "Choppy / below the 200-day average (longer-term weak)"
    elif sma50:
        trend = "Above 50-day average" if spot > sma50 else "Below 50-day average"
    else:
        trend = "Not enough history for a trend read"

    rsi_state = "n/a"
    if rsi is not None:
        rsi_state = ("overbought (extended, pullback risk)" if rsi >= 70
                     else "oversold (washed out, bounce possible)" if rsi <= 30
                     else "neutral")
    macd_state = "n/a"
    if macd_hist is not None:
        macd_state = ("bullish — momentum building" if macd_hist > 0
                      else "bearish — momentum fading")

    notes: list[str] = [trend]
    if rsi is not None:
        notes.append(f"RSI {rsi} — {rsi_state}")
    if macd_hist is not None:
        notes.append(f"MACD {macd_state}")
    if resistances:
        notes.append(f"Nearest resistance ${resistances[0]:,.2f} "
                     f"({(resistances[0]-spot)/spot:+.1%}) — a break above is bullish")
    if supports:
        notes.append(f"Nearest support ${supports[0]:,.2f} "
                     f"({(supports[0]-spot)/spot:+.1%}) — a break below is bearish")

    read = TechnicalRead(
        ticker=ticker, spot=spot, data_source=source, trend=trend,
        sma20=sma20, sma50=sma50, sma200=sma200,
        rsi=rsi, rsi_state=rsi_state,
        macd=macd, macd_signal=macd_signal, macd_hist=macd_hist, macd_state=macd_state,
        supports=supports, resistances=resistances,
        range_high=range_high, range_low=range_low,
        breakout_above=breakout_above, breakdown_below=breakdown_below,
        upside_target=upside_target, notes=notes,
    )
    return asdict(read)
