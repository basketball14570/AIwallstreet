"""Historical labelling: define what an "explosive move" is, post-hoc.

Triple-barrier style labelling on forward returns. A flow event is labelled
positive (1) if, within the horizon, the underlying ran up past `up_barrier`
*before* breaching `down_barrier`. This avoids look-ahead bias and rewards the
asymmetric upside the platform targets.
"""
from __future__ import annotations

import pandas as pd


def label_triple_barrier(
    prices: pd.Series,
    entry_idx: int,
    up_barrier: float = 0.20,
    down_barrier: float = -0.10,
    horizon: int = 5,
) -> dict:
    """`prices` is a forward price series starting at the entry bar.

    Returns realised stats + binary label for one event.
    """
    entry = prices.iloc[entry_idx]
    window = prices.iloc[entry_idx : entry_idx + horizon + 1]
    rets = window / entry - 1.0
    max_runup = float(rets.max())
    max_dd = float(rets.min())

    label = 0
    for r in rets:
        if r >= up_barrier:
            label = 1
            break
        if r <= down_barrier:
            label = 0
            break

    end = min(entry_idx + horizon, len(prices) - 1)
    return {
        "ret_1d": float(prices.iloc[min(entry_idx + 1, len(prices) - 1)] / entry - 1),
        "ret_5d": float(prices.iloc[end] / entry - 1),
        "max_runup": max_runup,
        "max_drawdown": max_dd,
        "label": label,
    }
