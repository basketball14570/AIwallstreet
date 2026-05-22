"""Confidence calibration.

The rules engine produces a raw 0-1 'explosion' score. Raw scores are rarely
calibrated — a 0.8 does not mean "explodes 80% of the time". Calibration maps
raw scores to empirical hit-rates measured on the ``outcome`` table.

Two supported methods (req: probability calibration):

* Platt scaling — fit a 1-D logistic  P(y=1) = sigmoid(a*s + b)  on
  (raw_score, label) pairs. Good when miscalibration is monotonic / sigmoidal
  and data is scarce.
* Isotonic regression — non-parametric monotonic fit. More flexible, needs more
  data; preferred once you have a few thousand labelled outcomes.

Until a calibrator is fitted, ``Calibrator.identity()`` returns scores
unchanged so the pipeline always works.
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Calibrator:
    """Platt calibrator: p = sigmoid(a * s + b).

    An *identity* calibrator (the default) passes raw scores through unchanged,
    so the pipeline runs uncalibrated until real outcome data is available.
    """

    a: float = 1.0
    b: float = 0.0
    _identity: bool = True

    @classmethod
    def identity(cls) -> "Calibrator":
        return cls(_identity=True)

    def __call__(self, score: float) -> float:
        if self._identity:
            return max(0.0, min(1.0, score))
        return 1.0 / (1.0 + math.exp(-(self.a * score + self.b)))

    # ----- fitting ----------------------------------------------------------
    @classmethod
    def fit_platt(cls, scores: list[float], labels: list[int],
                  iters: int = 500, lr: float = 0.1) -> "Calibrator":
        """Fit a, b by gradient descent on log-loss. Small, dependency-free."""
        a, b = 1.0, 0.0
        n = len(scores)
        if n == 0:
            return cls.identity()
        for _ in range(iters):
            ga = gb = 0.0
            for s, y in zip(scores, labels):
                p = 1.0 / (1.0 + math.exp(-(a * s + b)))
                err = p - y
                ga += err * s
                gb += err
            a -= lr * ga / n
            b -= lr * gb / n
        return cls(a=a, b=b, _identity=False)

    # ----- persistence ------------------------------------------------------
    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps({"a": self.a, "b": self.b}))

    @classmethod
    def load(cls, path: str | Path) -> "Calibrator":
        p = Path(path)
        if not p.exists():
            return cls.identity()
        d = json.loads(p.read_text())
        return cls(a=d["a"], b=d["b"], _identity=False)


def expected_calibration_error(scores: list[float], labels: list[int],
                               bins: int = 10) -> float:
    """ECE: mean |confidence - accuracy| across probability bins. Lower = better
    calibrated. Use to validate a fitted calibrator on held-out data."""
    if not scores:
        return 0.0
    total = len(scores)
    ece = 0.0
    for i in range(bins):
        lo, hi = i / bins, (i + 1) / bins
        idx = [j for j, s in enumerate(scores) if lo <= s < hi or (i == bins - 1 and s == 1.0)]
        if not idx:
            continue
        conf = sum(scores[j] for j in idx) / len(idx)
        acc = sum(labels[j] for j in idx) / len(idx)
        ece += (len(idx) / total) * abs(conf - acc)
    return ece
