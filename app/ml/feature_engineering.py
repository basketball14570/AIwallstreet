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
    "social_score",
    "news_score",
    "historical_similarity",
]


def to_matrix(df: pd.DataFrame) -> np.ndarray:
    X = df.reindex(columns=FEATURE_COLUMNS).copy()
    X["at_midpoint"] = X["at_midpoint"].astype(float)
    # Log-scale heavy-tailed magnitudes; impute missing with column median.
    for col in ("float_shares", "rel_options_volume"):
        X[col] = np.log1p(X[col].clip(lower=0))
    return X.fillna(X.median(numeric_only=True)).to_numpy(dtype=float)
