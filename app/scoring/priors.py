"""Per-ticker hit-rate priors.

The nightly retrain measures, for each underlying, what fraction of its scored
flow actually went on to explode (the realised ``outcome`` label). That becomes
a Bayesian-shrunk prior — "how often does flow on this name pay off?" — which
the engine folds into the explosion boost.

The artifact is a small JSON written by ``app.jobs.retrain`` and hot-reloaded
here (mtime-checked) so a fresh prior takes effect with no restart.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

PRIORS_PATH = "models/ticker_priors.json"
_RELOAD_INTERVAL = 30.0


def shrink_hit_rate(positives: int, total: int, global_rate: float,
                    pseudo: float = 20.0) -> float:
    """Empirical-Bayes shrinkage of a per-ticker rate toward the global rate.

    With few samples the estimate sits near ``global_rate``; as ``total`` grows
    it converges to the raw per-ticker rate. ``pseudo`` is the strength of the
    prior in pseudo-observations."""
    if total <= 0:
        return global_rate
    return (positives + pseudo * global_rate) / (total + pseudo)


class TickerPriors:
    """Maps ticker -> shrunk historical hit-rate, with a global fallback."""

    def __init__(self, rates: dict[str, float] | None = None, global_rate: float = 0.0):
        self.rates = rates or {}
        self.global_rate = global_rate

    def get(self, ticker: str) -> float:
        return self.rates.get(ticker.upper(), self.global_rate)

    def to_dict(self) -> dict:
        return {"global": self.global_rate, "rates": self.rates}

    @classmethod
    def from_dict(cls, d: dict) -> "TickerPriors":
        return cls(rates=d.get("rates", {}), global_rate=float(d.get("global", 0.0)))

    def save(self, path: str | Path = PRIORS_PATH) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict()))

    @classmethod
    def load(cls, path: str | Path = PRIORS_PATH) -> "TickerPriors | None":
        p = Path(path)
        if not p.exists():
            return None
        return cls.from_dict(json.loads(p.read_text()))


_UNSET = object()
_priors: TickerPriors | None = None
_mtime: object = _UNSET
_checked = 0.0


def get_priors() -> TickerPriors | None:
    """Process-wide cached priors, reloaded when the artifact changes."""
    global _priors, _mtime, _checked
    now = time.monotonic()
    if now - _checked >= _RELOAD_INTERVAL:
        _checked = now
        try:
            m = Path(PRIORS_PATH).stat().st_mtime
        except OSError:
            m = None
        if m != _mtime:
            _mtime = m
            _priors = TickerPriors.load() if m else None
    return _priors
