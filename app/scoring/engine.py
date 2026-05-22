"""Weighted, explainable scoring engine.

Design goals
------------
* Transparent: every probability decomposes into named component scores and
  human-readable reasons (the "explain WHY" requirement).
* Probabilistic, not deterministic: emits 0-1 probabilities (optionally
  calibrated against realised outcomes) and a 0-100 confidence.
* False-positive minimised: bullish setups require *corroboration across
  independent axes* (soft-AND / geometric mean), and are damped by explicit
  fake-flow and institutional-hedging detectors that act as gates.
* Swappable: an ML model (app/ml) can override the four probabilities while
  reusing these component scores as features.

All component scores are in [0, 1]. Weights live in `ScoringWeights` so they
can be tuned, A/B tested, or learned without touching logic.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.flow import FlowFeatureVector
from app.scoring.calibration import Calibrator
from app.scoring.normalize import clamp, logistic, ramp, soft_and


@dataclass
class ScoringWeights:
    # Component weights feeding the explosion score (sum ~ 1.0).
    conviction: float = 0.25
    volume_confirmation: float = 0.20
    squeeze_fuel: float = 0.25
    catalyst: float = 0.15
    geometry: float = 0.10
    historical: float = 0.05

    # Gate strengths (multiplicative damping of bullish probabilities).
    fake_flow_damping: float = 0.85
    hedging_damping: float = 0.70


@dataclass
class Components:
    conviction: float = 0.0
    volume_confirmation: float = 0.0
    squeeze_fuel: float = 0.0
    gamma_squeeze: float = 0.0
    catalyst: float = 0.0
    geometry: float = 0.0
    historical: float = 0.0
    urgency: float = 0.0
    institutional_hedging: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, float]:
        return {
            k: round(getattr(self, k), 4)
            for k in (
                "conviction", "volume_confirmation", "squeeze_fuel", "gamma_squeeze",
                "catalyst", "geometry", "historical", "urgency",
                "institutional_hedging",
            )
        }


class ScoringEngine:
    def __init__(self, weights: ScoringWeights | None = None,
                 calibrator: Calibrator | None = None):
        self.w = weights or ScoringWeights()
        # Calibrators map raw scores -> empirical hit-rate. Identity until fitted.
        self.cal_explosion = calibrator or Calibrator.identity()
        self.cal_squeeze = Calibrator.identity()

    # ----- urgency ----------------------------------------------------------
    def _urgency(self, f: FlowFeatureVector, c: Components) -> float:
        """How aggressively is the buyer demanding fills *now*?
        Blends sweep aggressiveness, repeat cadence and premium size."""
        prem = logistic(f.premium, x0=250_000, k=1.0 / 150_000)
        u = 0.45 * f.sweep_urgency + 0.30 * ramp(f.repeated_sweeps, 1, 5) + 0.25 * prem
        if u > 0.75:
            c.reasons.append("High execution urgency — repeated aggressive sweeps")
        return clamp(u)

    # ----- conviction -------------------------------------------------------
    def _conviction(self, f: FlowFeatureVector, c: Components) -> float:
        s = 0.45 * f.ask_side_ratio + 0.35 * f.sweep_urgency
        s += 0.20 * ramp(f.repeated_sweeps, 1, 5)
        # Sequence corroboration (0 by default -> no effect on snapshot scoring).
        s += 0.12 * clamp(f.seq_cadence_accel) + 0.08 * clamp(f.seq_strike_ladder)
        if f.at_midpoint:
            s *= 0.5
            c.reasons.append("Midpoint fill reduces directional conviction")
        if f.ask_side_ratio >= 1.0 and f.sweep_urgency > 0.6:
            c.reasons.append("Aggressive ask-side sweep — buyer paying up")
        if f.repeated_sweeps >= 3:
            c.reasons.append(f"{f.repeated_sweeps} repeated sweeps in window")
        if f.seq_cadence_accel > 0.4:
            c.reasons.append("Sweep cadence accelerating — order flow intensifying")
        if f.seq_strike_ladder > 0.6:
            c.reasons.append("Strikes laddering up — scaling into higher calls")
        return clamp(s)

    # ----- volume confirmation ---------------------------------------------
    def _volume_confirmation(self, f: FlowFeatureVector, c: Components) -> float:
        opt = ramp(f.rel_options_volume, 2, 10)
        rvol = ramp(f.stock_rvol, 1.5, 5)
        oi = ramp(f.oi_change_ratio, 0.2, 1.5)
        s = 0.45 * opt + 0.30 * rvol + 0.25 * oi
        if f.rel_options_volume >= 5:
            c.reasons.append(f"Options volume {f.rel_options_volume:.1f}x normal")
        if f.stock_rvol >= 2:
            c.reasons.append(f"Underlying RVOL {f.stock_rvol:.1f}x confirms interest")
        return clamp(s)

    # ----- gamma squeeze ----------------------------------------------------
    def _gamma_squeeze(self, f: FlowFeatureVector, c: Components) -> float:
        """Dealer-driven reflexive bid. Requires NEGATIVE dealer gamma (dealers
        short gamma must buy into strength) AND near-dated OTM calls (where the
        hedging delta ramps fastest)."""
        if f.dealer_gamma is None or f.dealer_gamma >= 0 or f.is_put:
            return 0.0
        short_gamma = ramp(-f.dealer_gamma, 0.0, 1.0)
        near_dated = 1.0 - ramp(f.dte, 0, 30)
        otm_call = ramp(f.otm_pct, 0.0, 0.20)  # OTM calls drive the gamma ramp
        g = soft_and(short_gamma, max(near_dated, 0.2), max(otm_call, 0.2))
        if g > 0.4:
            c.reasons.append("Negative dealer gamma + near-dated OTM calls — gamma squeeze risk")
        return clamp(g)

    # ----- squeeze fuel -----------------------------------------------------
    def _squeeze_fuel(self, f: FlowFeatureVector, c: Components) -> float:
        if f.float_shares is None:
            return 0.0
        float_m = f.float_shares / 1_000_000.0
        low_float = clamp((100.0 - float_m) / 95.0)
        si = ramp(f.short_interest_pct or 0.0, 10, 30)
        borrow = ramp(f.borrow_rate or 0.0, 20, 100)
        s = 0.30 * low_float + 0.28 * si + 0.14 * borrow + 0.28 * c.gamma_squeeze
        if float_m < 20:
            c.reasons.append(f"Low float {float_m:.0f}M amplifies moves")
        if (f.short_interest_pct or 0) >= 20:
            c.reasons.append(f"Short interest {f.short_interest_pct:.0f}% — squeeze fuel")
        return clamp(s)

    # ----- catalyst / geometry ---------------------------------------------
    def _catalyst(self, f: FlowFeatureVector, c: Components) -> float:
        s = 0.6 * clamp(f.social_score) + 0.4 * clamp(f.news_score)
        if f.social_score > 0.6:
            c.reasons.append("Elevated social/retail attention")
        if f.news_score > 0.6:
            c.reasons.append("Fresh news catalyst aligned with flow")
        return clamp(s)

    def _geometry(self, f: FlowFeatureVector, c: Components) -> float:
        otm = clamp(1.0 - abs(f.otm_pct - 0.07) / 0.15)  # peak ~ +7% OTM
        dte = 1.0 - ramp(f.dte, 0, 45)
        s = 0.6 * otm + 0.4 * dte
        if 0 < f.otm_pct < 0.15 and f.dte < 21:
            c.reasons.append("Near-dated OTM contracts — speculative footprint")
        return clamp(s)

    # ----- gates: fake flow + institutional hedging ------------------------
    def _fake_flow_prob(self, f: FlowFeatureVector, c: Components) -> float:
        s = 0.0
        if f.at_midpoint:
            s += 0.35
        if f.ask_side_ratio == 0.0 and not f.at_midpoint:
            s += 0.25  # bid-side / sold
        s += 0.25 * (1 - ramp(f.rel_options_volume, 1, 5))  # no volume backing
        s += 0.15 * (1 - f.sweep_urgency)
        prob = clamp(s)
        if prob > 0.6:
            c.reasons.append("Pattern resembles low-quality / noise flow")
        return prob

    def _institutional_hedging(self, f: FlowFeatureVector, c: Components) -> float:
        """Detect routine hedging / positioning rather than speculation:
        spreads (collars/risk-reversals), protective puts on mega-caps, passive
        midpoint/bid fills with no catalyst."""
        s = 0.0
        if f.is_spread:
            s += 0.40  # multi-leg structures are overwhelmingly hedges
        big_cap = (f.float_shares or 0) > 500_000_000
        if f.is_put and big_cap and abs(f.otm_pct) < 0.05:
            s += 0.30  # protective put near the money on a large name
        passive = f.ask_side_ratio <= 0.5  # mid or bid
        if passive and f.premium > 500_000 and f.dte > 30:
            s += 0.20  # big, patient, far-dated, not lifting offers
        if (f.social_score + f.news_score) < 0.4:
            s += 0.10  # no catalyst to justify a speculative bet
        prob = clamp(s)
        if prob > 0.5:
            c.reasons.append("Footprint consistent with institutional hedging")
        return prob

    # ----- public API -------------------------------------------------------
    def score(self, f: FlowFeatureVector) -> tuple[Components, dict[str, float]]:
        c = Components()
        c.urgency = self._urgency(f, c)
        c.conviction = self._conviction(f, c)
        c.volume_confirmation = self._volume_confirmation(f, c)
        c.gamma_squeeze = self._gamma_squeeze(f, c)
        c.squeeze_fuel = self._squeeze_fuel(f, c)
        c.catalyst = self._catalyst(f, c)
        c.geometry = self._geometry(f, c)
        c.historical = clamp(f.historical_similarity)

        fake = self._fake_flow_prob(f, c)
        hedge = self._institutional_hedging(f, c)
        c.institutional_hedging = hedge
        # Combined damping gate: bullish probs are suppressed when EITHER the
        # flow looks like noise OR like a hedge. This is the FP-control lever.
        damp = (1.0 - self.w.fake_flow_damping * fake) * (1.0 - self.w.hedging_damping * hedge)

        w = self.w
        base = (
            w.conviction * c.conviction
            + w.volume_confirmation * c.volume_confirmation
            + w.squeeze_fuel * c.squeeze_fuel
            + w.catalyst * c.catalyst
            + w.geometry * c.geometry
            + w.historical * c.historical
        )

        momentum = clamp(
            (0.5 * c.conviction + 0.35 * c.volume_confirmation + 0.15 * c.geometry) * damp
        )
        squeeze = clamp(
            (0.55 * c.squeeze_fuel + 0.25 * c.conviction + 0.20 * c.volume_confirmation)
            * damp
        )
        # Explosion = corroboration across the three independent legs (soft-AND),
        # lifted by catalyst/history, then damped. One strong leg cannot trigger.
        core = soft_and(c.conviction, c.volume_confirmation, c.squeeze_fuel)
        boost = 0.7 + 0.3 * max(c.catalyst, c.historical, c.geometry)
        explosion = clamp(core * boost * damp)

        probs = {
            "fake_flow_prob": round(fake, 4),
            "hedging_prob": round(hedge, 4),
            "momentum_prob": round(momentum, 4),
            "squeeze_prob": round(self.cal_squeeze(squeeze), 4),
            "explosion_prob": round(self.cal_explosion(explosion), 4),
        }
        return c, probs
