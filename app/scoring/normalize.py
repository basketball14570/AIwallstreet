"""Normalization primitives for the scoring engine.

Every raw signal lives on a different scale (premium in dollars, RVOL as a
ratio, short interest as a percent). To combine them we map each onto a common
[0, 1] "evidence" axis. Three methods, chosen per-signal by its distribution:

* ``ramp``      — bounded linear map. Use when sensible domain thresholds are
                  known (e.g. RVOL between 2x and 10x). Cheap, interpretable.
* ``logistic``  — smooth saturating squash. Use for heavy-tailed magnitudes
                  where extremes shouldn't dominate (premium, gamma notional).
* ``robust_z``  — median/IQR z-score then squashed. Use when you have a live
                  reference distribution per ticker and want outlier-resistant
                  standardization (preferred over mean/std for fat tails).

All functions return values in [0, 1] (``robust_z`` after squashing).
"""
from __future__ import annotations

import math


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def ramp(x: float, lo: float, hi: float) -> float:
    """Linear 0->1 between lo and hi, clamped outside."""
    if hi <= lo:
        return 0.0
    return clamp((x - lo) / (hi - lo))


def logistic(x: float, x0: float, k: float) -> float:
    """Logistic squash. x0 = midpoint (maps to 0.5), k = steepness.

    1 / (1 + e^{-k (x - x0)}).  Larger k -> sharper transition.
    """
    try:
        return 1.0 / (1.0 + math.exp(-k * (x - x0)))
    except OverflowError:
        return 0.0 if x < x0 else 1.0


def robust_z(x: float, median: float, iqr: float) -> float:
    """Outlier-resistant z-score: (x - median) / (1.349 * IQR_scaled).

    IQR is scaled so that for a normal distribution this matches the usual
    z-score (1.349 ~ IQR/sigma). Returns the raw z; squash with ``logistic``
    or ``norm_cdf`` to land in [0, 1].
    """
    if iqr <= 0:
        return 0.0
    return (x - median) / (1.349 * iqr)


def norm_cdf(z: float) -> float:
    """Standard-normal CDF via erf — turns a z-score into a percentile in [0,1]."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def soft_and(*xs: float) -> float:
    """Corroboration operator. Geometric mean: every factor must be present for
    a high result, so a single strong signal cannot carry the score. This is the
    core false-positive lever — it punishes one-dimensional 'evidence'."""
    xs = [clamp(x) for x in xs if x is not None]
    if not xs:
        return 0.0
    prod = 1.0
    for x in xs:
        prod *= max(x, 1e-9)
    return prod ** (1.0 / len(xs))
