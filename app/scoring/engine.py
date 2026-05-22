"""Weighted, explainable scoring engine.

Design goals
------------
* Transparent: every probability decomposes into named component scores and
  human-readable reasons (the "explain WHY" requirement).
* Probabilistic, not deterministic: emits calibrated-ish 0-1 probabilities and
  a 0-100 confidence rather than buy/sell signals.
* Swappable: the rules engine is the MVP. An ML model (see app/ml) can override
  the four probabilities while reusing these component scores as features.

All component scores are in [0, 1]. Weights live in `ScoringWeights` so they
can be tuned, A/B tested, or learned without touching logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.flow import FlowFeatureVector


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _ramp(x: float, lo: float, hi: float) -> float:
    """Linear 0->1 ramp between lo and hi."""
    if hi <= lo:
        return 0.0
    return _clamp((x - lo) / (hi - lo))


@dataclass
class ScoringWeights:
    # Component weights feeding the explosion score.
    conviction: float = 0.25
    volume_confirmation: float = 0.20
    squeeze_fuel: float = 0.25
    catalyst: float = 0.15
    geometry: float = 0.10
    historical: float = 0.05

    # Fake-flow penalty applied multiplicatively to bullish probabilities.
    fake_flow_damping: float = 0.85


@dataclass
class Components:
    conviction: float = 0.0
    volume_confirmation: float = 0.0
    squeeze_fuel: float = 0.0
    catalyst: float = 0.0
    geometry: float = 0.0
    historical: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, float]:
        return {
            "conviction": round(self.conviction, 4),
            "volume_confirmation": round(self.volume_confirmation, 4),
            "squeeze_fuel": round(self.squeeze_fuel, 4),
            "catalyst": round(self.catalyst, 4),
            "geometry": round(self.geometry, 4),
            "historical": round(self.historical, 4),
        }


class ScoringEngine:
    def __init__(self, weights: ScoringWeights | None = None):
        self.w = weights or ScoringWeights()

    # ----- component scores -------------------------------------------------
    def _conviction(self, f: FlowFeatureVector, c: Components) -> float:
        s = 0.45 * f.ask_side_ratio + 0.35 * f.sweep_urgency
        s += 0.20 * _ramp(f.repeated_sweeps, 1, 5)
        if f.at_midpoint:
            s *= 0.5
            c.reasons.append("Midpoint fill reduces directional conviction")
        if f.ask_side_ratio >= 1.0 and f.sweep_urgency > 0.6:
            c.reasons.append("Aggressive ask-side sweep — buyer paying up")
        if f.repeated_sweeps >= 3:
            c.reasons.append(f"{f.repeated_sweeps} repeated sweeps in window")
        return _clamp(s)

    def _volume_confirmation(self, f: FlowFeatureVector, c: Components) -> float:
        opt = _ramp(f.rel_options_volume, 2, 10)
        rvol = _ramp(f.stock_rvol, 1.5, 5)
        oi = _ramp(f.oi_change_ratio, 0.2, 1.5)
        s = 0.45 * opt + 0.30 * rvol + 0.25 * oi
        if f.rel_options_volume >= 5:
            c.reasons.append(f"Options volume {f.rel_options_volume:.1f}x normal")
        if f.stock_rvol >= 2:
            c.reasons.append(f"Underlying RVOL {f.stock_rvol:.1f}x confirms interest")
        return _clamp(s)

    def _squeeze_fuel(self, f: FlowFeatureVector, c: Components) -> float:
        if f.float_shares is None:
            return 0.0
        # Low float: <20M is potent. Map 100M->0, 5M->1.
        float_m = f.float_shares / 1_000_000.0
        low_float = _clamp((100.0 - float_m) / 95.0)
        si = _ramp(f.short_interest_pct or 0.0, 10, 30)
        borrow = _ramp(f.borrow_rate or 0.0, 20, 100)
        # Negative dealer gamma => dealers buy into strength (squeeze fuel).
        gamma = 0.0
        if f.dealer_gamma is not None and f.dealer_gamma < 0:
            gamma = _ramp(-f.dealer_gamma, 0, 1)
        s = 0.35 * low_float + 0.30 * si + 0.15 * borrow + 0.20 * gamma
        if float_m < 20:
            c.reasons.append(f"Low float {float_m:.0f}M amplifies moves")
        if (f.short_interest_pct or 0) >= 20:
            c.reasons.append(f"Short interest {f.short_interest_pct:.0f}% — squeeze fuel")
        if gamma > 0.3:
            c.reasons.append("Negative dealer gamma — hedging accelerates upside")
        return _clamp(s)

    def _catalyst(self, f: FlowFeatureVector, c: Components) -> float:
        s = 0.6 * _clamp(f.social_score) + 0.4 * _clamp(f.news_score)
        if f.social_score > 0.6:
            c.reasons.append("Elevated social/retail attention")
        if f.news_score > 0.6:
            c.reasons.append("Fresh news catalyst aligned with flow")
        return _clamp(s)

    def _geometry(self, f: FlowFeatureVector, c: Components) -> float:
        # Slightly-OTM, near-dated calls are the classic speculative footprint.
        otm = 1.0 - abs(f.otm_pct - 0.07) / 0.15  # peak around +7% OTM
        otm = _clamp(otm)
        dte = 1.0 - _ramp(f.dte, 0, 45)  # nearer dated => higher
        s = 0.6 * otm + 0.4 * dte
        if 0 < f.otm_pct < 0.15 and f.dte < 21:
            c.reasons.append("Near-dated OTM contracts — speculative footprint")
        return _clamp(s)

    # ----- fake flow --------------------------------------------------------
    def _fake_flow_prob(self, f: FlowFeatureVector, c: Components) -> float:
        s = 0.0
        if f.at_midpoint:
            s += 0.35
        if f.ask_side_ratio == 0.0 and not f.at_midpoint:
            s += 0.25  # bid-side / sold
        s += 0.25 * (1 - _ramp(f.rel_options_volume, 1, 5))  # no volume backing
        s += 0.15 * (1 - f.sweep_urgency)
        prob = _clamp(s)
        if prob > 0.6:
            c.reasons.append("Pattern resembles hedging / low-quality flow")
        return prob

    # ----- public API -------------------------------------------------------
    def score(self, f: FlowFeatureVector) -> tuple[Components, dict[str, float]]:
        c = Components()
        c.conviction = self._conviction(f, c)
        c.volume_confirmation = self._volume_confirmation(f, c)
        c.squeeze_fuel = self._squeeze_fuel(f, c)
        c.catalyst = self._catalyst(f, c)
        c.geometry = self._geometry(f, c)
        c.historical = _clamp(f.historical_similarity)

        fake = self._fake_flow_prob(f, c)
        damp = 1.0 - self.w.fake_flow_damping * fake

        w = self.w
        base = (
            w.conviction * c.conviction
            + w.volume_confirmation * c.volume_confirmation
            + w.squeeze_fuel * c.squeeze_fuel
            + w.catalyst * c.catalyst
            + w.geometry * c.geometry
            + w.historical * c.historical
        )  # already weighted to ~[0,1] since weights sum to 1

        momentum = _clamp(
            (0.5 * c.conviction + 0.35 * c.volume_confirmation + 0.15 * c.geometry) * damp
        )
        squeeze = _clamp(
            (0.55 * c.squeeze_fuel + 0.25 * c.conviction + 0.20 * c.volume_confirmation)
            * damp
        )
        # Explosion needs BOTH directional conviction and squeeze fuel present;
        # use a soft AND (geometric-ish) so one strong leg can't carry it alone.
        explosion = _clamp(base * (0.5 + 0.5 * min(momentum, squeeze + 0.3)) * damp)

        probs = {
            "fake_flow_prob": round(fake, 4),
            "momentum_prob": round(momentum, 4),
            "squeeze_prob": round(squeeze, 4),
            "explosion_prob": round(explosion, 4),
        }
        return c, probs
