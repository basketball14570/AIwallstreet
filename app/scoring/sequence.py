"""Intra-event sequence tracking.

Single options prints are weak evidence; the *sequence* is where the edge
lives — sweeps arriving faster (cadence acceleration), strikes laddering up
over minutes, premium velocity rising. This tracker maintains a rolling
per-ticker window and derives those temporal features for the live engine.

It also defines the canonical sequence representation that a future Temporal
CNN / transformer will consume (`build_sequences` in app/ml/sequences.py reads
the same per-key ordering back out of `raw_flow`).
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from app.schemas.flow import ContractType, FlowEvent


@dataclass
class SequenceFeatures:
    cadence_accel: float = 0.0
    strike_ladder: float = 0.0
    premium_velocity: float = 0.0
    count: int = 0


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class SequenceTracker:
    """Rolling window of recent prints per ticker. Stateful; one per worker.

    `record` is O(window) and returns features computed *including* the new
    event, mirroring how the live pipeline calls it.
    """

    def __init__(self, window_sec: float = 1800.0, max_events: int = 200):
        self.window = window_sec
        self.max_events = max_events
        # ticker -> deque[(ts, strike, premium, is_call, is_sweep)]
        self._events: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_events))

    def record(self, event: FlowEvent, now: float | None = None) -> SequenceFeatures:
        now = now if now is not None else time.time()
        dq = self._events[event.ticker]
        dq.append((now, event.strike, event.premium,
                   event.contract_type == ContractType.CALL, event.is_sweep))
        # Evict outside the window.
        while dq and now - dq[0][0] > self.window:
            dq.popleft()

        ts = [e[0] for e in dq]
        return SequenceFeatures(
            cadence_accel=_cadence_accel(ts),
            strike_ladder=_strike_ladder(dq),
            premium_velocity=_premium_velocity(dq, self.window),
            count=len(dq),
        )


def _cadence_accel(ts: list[float]) -> float:
    """Compare the most-recent inter-arrival gaps to the earlier ones. Positive
    and ->1 when prints are arriving progressively faster."""
    if len(ts) < 4:
        return 0.0
    gaps = [b - a for a, b in zip(ts, ts[1:])]
    half = len(gaps) // 2
    earlier = sum(gaps[:half]) / half
    recent = sum(gaps[half:]) / (len(gaps) - half)
    if earlier <= 0:
        return 0.0
    return _clamp((earlier - recent) / earlier)


def _strike_ladder(dq) -> float:
    """Fraction of consecutive call prints whose strike steps strictly up over
    time — the 'laddering' footprint of someone scaling into higher strikes."""
    calls = [(ts, strike) for ts, strike, _prem, is_call, _sw in dq if is_call]
    if len(calls) < 3:
        return 0.0
    calls.sort(key=lambda x: x[0])
    ups = sum(1 for (_, s0), (_, s1) in zip(calls, calls[1:]) if s1 > s0)
    return ups / (len(calls) - 1)


def _premium_velocity(dq, window: float) -> float:
    total = sum(e[2] for e in dq)
    return total / window if window > 0 else 0.0
