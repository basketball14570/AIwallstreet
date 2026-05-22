"""Assemble per-key event sequences for temporal models.

The live `SequenceTracker` derives a few summary stats in real time; this module
reconstructs the *full ordered sequence* from stored `raw_flow` + `flow_features`
so that — once enough data has accumulated — a Temporal CNN / LSTM / light
transformer can be trained on the raw progression (sweep cadence, strike
laddering, urgency ramp) rather than a single snapshot.

Output is a right-aligned, zero-padded `(N, max_len, F)` tensor plus a mask, the
standard input shape for sequence models. Right-alignment keeps the most recent
prints (the decision-relevant ones) and pads/truncates the older tail.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.ml.feature_engineering import FEATURE_COLUMNS


def build_sequences(
    df: pd.DataFrame,
    key_cols: tuple[str, ...] = ("ticker",),
    feature_cols: list[str] | None = None,
    time_col: str = "observed_at",
    max_len: int = 50,
) -> list[tuple[tuple, np.ndarray]]:
    """Group rows by key, sort by time, emit (key, (T<=max_len, F)) arrays."""
    feats = feature_cols or FEATURE_COLUMNS
    out: list[tuple[tuple, np.ndarray]] = []
    df = df.sort_values(time_col)
    for key, g in df.groupby(list(key_cols), sort=False):
        mat = g.reindex(columns=feats).fillna(0.0).to_numpy(dtype=float)[-max_len:]
        key_t = key if isinstance(key, tuple) else (key,)
        out.append((key_t, mat))
    return out


def to_padded_tensor(
    sequences: list[tuple[tuple, np.ndarray]],
    max_len: int = 50,
    n_features: int | None = None,
) -> tuple[np.ndarray, np.ndarray, list[tuple]]:
    """Right-aligned zero-padded tensor + boolean mask.

    Returns (X[N, max_len, F], mask[N, max_len], keys). mask=True at real steps.
    """
    n = len(sequences)
    f = n_features or (sequences[0][1].shape[1] if n else len(FEATURE_COLUMNS))
    X = np.zeros((n, max_len, f), dtype=float)
    mask = np.zeros((n, max_len), dtype=bool)
    keys = []
    for i, (key, mat) in enumerate(sequences):
        t = min(len(mat), max_len)
        X[i, max_len - t:] = mat[-t:]      # right-align
        mask[i, max_len - t:] = True
        keys.append(key)
    return X, mask, keys


async def load_sequence_frame(session) -> pd.DataFrame:
    """Join raw_flow + flow_features into a time-ordered frame for sequencing."""
    from sqlalchemy import select

    from app.db.models import FlowFeatures, RawFlow

    cols = [getattr(FlowFeatures, c) for c in FEATURE_COLUMNS]
    stmt = (select(RawFlow.ticker, RawFlow.observed_at, *cols)
            .join(FlowFeatures, FlowFeatures.flow_id == RawFlow.id)
            .order_by(RawFlow.ticker, RawFlow.observed_at))
    rows = (await session.execute(stmt)).all()
    return pd.DataFrame(rows, columns=["ticker", "observed_at", *FEATURE_COLUMNS])
