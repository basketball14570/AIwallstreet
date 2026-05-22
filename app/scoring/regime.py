"""Market-regime detection.

The same flow means different things in different tapes: an aggressive OTM call
sweep is highly predictive in a squeeze-friendly, short-gamma small-cap tape and
mostly noise in low-volatility macro chop or a risk-off unwind. So we detect the
regime from macro inputs and use it to *dynamically damp or boost* bullish
probabilities and tighten the alert threshold — the highest-leverage
false-positive control after corroboration.

Design: detection is a transparent rule layer over normalised macro signals
(same philosophy as the per-event engine). A `Regime` carries multiplicative
adjustments so it composes cleanly with the per-event score and stays
explainable. NEUTRAL is the identity (no effect), so the engine is unchanged
when no macro context is supplied.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MacroContext:
    """Market-wide reference data sampled periodically (not per ticker)."""

    vix: float | None = None              # spot VIX level
    vix_trend: float | None = None        # 5d fractional change in VIX
    spy_realized_vol: float | None = None # annualised, fractional
    breadth: float | None = None          # fraction of names above their 50d MA [0,1]
    putcall_skew: float | None = None     # index put/call ratio
    smallcap_rs: float | None = None      # IWM/SPY relative strength (>1 = small-caps lead)
    aggregate_gamma: float | None = None  # market-wide dealer gamma (<0 = short gamma)


@dataclass(frozen=True)
class Regime:
    name: str
    bullish_multiplier: float   # scales explosion/squeeze/momentum probs
    confidence_scale: float     # scales final 0-100 confidence (threshold lever)
    reason: str = ""


NEUTRAL = Regime("neutral", 1.0, 1.0)


def detect_regime(m: MacroContext) -> Regime:
    """Classify the tape. Order matters: risk-off overrides everything."""
    if m.vix is None:
        return NEUTRAL

    rising_vix = (m.vix_trend or 0.0) > 0.15
    short_gamma = (m.aggregate_gamma or 0.0) < 0
    weak_breadth = (m.breadth if m.breadth is not None else 0.5) < 0.4
    smallcaps_lead = (m.smallcap_rs or 1.0) > 1.02
    elevated_skew = (m.putcall_skew or 0.0) > 1.1

    # 1. Risk-off: high & rising vol, weak breadth, hedging demand. Fade specs.
    if m.vix >= 28 and rising_vix and (weak_breadth or elevated_skew):
        return Regime("risk_off", 0.55, 0.80,
                      "Risk-off tape (VIX spiking, weak breadth) — fading speculative flow")

    # 2. Squeeze-friendly: elevated-but-not-panic vol, short-gamma, small-caps
    #    leading. Reflexive dealer hedging amplifies upside. Modest boost only.
    if 18 <= m.vix < 30 and short_gamma and smallcaps_lead:
        return Regime("high_vol_squeeze", 1.12, 1.04,
                      "Squeeze-friendly tape (short gamma, small-caps leading)")

    # 3. Low-vol chop: complacent tape where speculative squeezes historically
    #    fizzle. Damp and raise the bar.
    if m.vix < 14 and not smallcaps_lead:
        return Regime("low_vol_chop", 0.75, 0.85,
                      "Low-vol macro chop — speculative setups historically fail here")

    return NEUTRAL
