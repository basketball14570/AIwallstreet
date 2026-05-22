"""Historical labelling — what counts as a successful setup.

Success definitions (per the product spec):

    Setup type   Success
    ----------   ----------------------------------
    Momentum     +10% within 2 trading days
    Squeeze      +20% within 5 trading days
    Scalping     +3% intraday
    Options      +50% option-premium increase

Labelling uses the **triple-barrier method** (Lopez de Prado): from the entry
bar, whichever barrier is touched first decides the label —

    * upper barrier (the success target)  -> label 1
    * lower barrier (a stop, to avoid rewarding paths that crater first) -> 0
    * vertical barrier (horizon expiry)   -> 0

This avoids look-ahead bias (we only use the realised forward path) and rewards
the *asymmetric, time-bounded* upside the platform targets, instead of plain
forward return which a slow grind or a -40%-then-recover path would corrupt.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

import numpy as np
import pandas as pd


class SetupType(str, Enum):
    MOMENTUM = "momentum"
    SQUEEZE = "squeeze"
    SCALPING = "scalping"
    OPTIONS = "options"


@dataclass(frozen=True)
class Barrier:
    up: float          # success target (fractional return, e.g. 0.10 = +10%)
    down: float        # stop barrier (negative)
    horizon: int       # max bars to reach target (trading days; 1 = intraday)


# Stops are set wider than half the target so we don't get stopped on noise but
# still reject paths that collapse before working. Tune from the loss matrix.
BARRIERS: dict[SetupType, Barrier] = {
    SetupType.MOMENTUM: Barrier(up=0.10, down=-0.06, horizon=2),
    SetupType.SQUEEZE:  Barrier(up=0.20, down=-0.10, horizon=5),
    SetupType.SCALPING: Barrier(up=0.03, down=-0.02, horizon=1),
    SetupType.OPTIONS:  Barrier(up=0.50, down=-0.30, horizon=5),
}


def triple_barrier_label(path: pd.Series, barrier: Barrier) -> dict:
    """`path` is the forward price (or option-mark) series, path[0] = entry.

    Returns realised stats + the binary label for ONE event.
    """
    entry = float(path.iloc[0])
    window = path.iloc[: barrier.horizon + 1]
    rets = window.to_numpy() / entry - 1.0

    label, touch = 0, "vertical"
    for r in rets[1:]:                       # skip entry bar itself
        if r >= barrier.up:
            label, touch = 1, "upper"
            break
        if r <= barrier.down:
            label, touch = 0, "lower"
            break

    return {
        "label": label,
        "touched": touch,
        "max_runup": float(rets.max()),
        "max_drawdown": float(rets.min()),
        "ret_final": float(rets[-1]),
        "bars_to_target": int(np.argmax(rets >= barrier.up)) if label else None,
    }


def label_event(price_path: pd.Series, setup: SetupType) -> dict:
    """Label a single event for a given setup type using its success barrier."""
    return triple_barrier_label(price_path, BARRIERS[setup])


def label_option_premium(option_marks: pd.Series, horizon: int = 5,
                         target: float = 0.50, stop: float = -0.30) -> dict:
    """Options-setup label off the *option* mark series (not the underlying),
    because a +50% premium move depends on delta, gamma, IV and theta together."""
    return triple_barrier_label(
        option_marks, Barrier(up=target, down=stop, horizon=horizon)
    )


def build_labels(events: pd.DataFrame, price_panel: dict[str, pd.DataFrame],
                 setup: SetupType) -> pd.DataFrame:
    """Vectorised labelling over many events.

    `events`      : rows with at least ['flow_id','ticker','observed_at'].
    `price_panel` : {ticker -> DataFrame indexed by date with a 'close' column}.
    Returns events augmented with label + outcome stats; rows without enough
    forward data are dropped (no leakage, no padding).
    """
    out = []
    b = BARRIERS[setup]
    for row in events.itertuples():
        px = price_panel.get(row.ticker)
        if px is None:
            continue
        fwd = px.loc[px.index >= row.observed_at, "close"]
        if len(fwd) < b.horizon + 1:
            continue
        rec = {"flow_id": row.flow_id, "ticker": row.ticker, "setup": setup.value}
        rec.update(label_event(fwd, setup))
        out.append(rec)
    return pd.DataFrame(out)
