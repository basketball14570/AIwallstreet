"""Convert stored FlowFeatures rows into a model-ready matrix.

The rules-engine component scores double as engineered features, so the ML
model and the rules engine share one feature space. This makes the ML model a
drop-in re-weighting / calibration of the same signals.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

FEATURE_COLUMNS = [
    "ask_side_ratio",
    "sweep_urgency",
    "repeated_sweeps",
    "at_midpoint",
    "otm_pct",
    "dte",
    "rel_options_volume",
    "stock_rvol",
    "oi_change_ratio",
    "float_shares",
    "short_interest_pct",
    "borrow_rate",
    "dealer_gamma",
    "iv_rank",
    "vol_oi",
    "is_opening",
    "days_to_earnings",
    "bullish_structure",
    "follow_through",
    "ticker_hit_rate",
    "social_score",
    "news_score",
    "historical_similarity",
]


def to_matrix(df: pd.DataFrame) -> np.ndarray:
    X = df.reindex(columns=FEATURE_COLUMNS).copy()
    # Coerce everything numeric (bools -> 0/1, None -> NaN).
    for col in FEATURE_COLUMNS:
        X[col] = pd.to_numeric(X[col], errors="coerce")
    # Impute missing with column median (0 when no data, e.g. single-row infer).
    medians = X.median(numeric_only=True).fillna(0.0)
    X = X.fillna(medians)
    # Log-scale heavy-tailed magnitudes.
    for col in ("float_shares", "rel_options_volume", "vol_oi", "days_to_earnings"):
        X[col] = np.log1p(X[col].clip(lower=0))
    return X.to_numpy(dtype=float)
