"""Tests for the backfill/retrain data-flywheel logic (DB-free cores)."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pandas as pd

from app.jobs.backfill import compute_outcome_record
from app.jobs.retrain import build_library
from app.ml.feature_engineering import FEATURE_COLUMNS
from app.ml.labeling import SetupType
from app.providers.prices import _synthetic_closes


def _series(values):
    idx = pd.bdate_range("2026-01-05", periods=len(values))
    return pd.Series(values, index=idx, dtype=float)


def test_outcome_record_squeeze_success():
    # +25% by day 3 -> squeeze label 1.
    rec = compute_outcome_record(1, "GME", _series([20, 21, 23, 25, 24, 24]))
    assert rec["label"] == 1
    assert rec["max_runup"] >= 0.20
    assert abs(rec["ret_1d"] - 0.05) < 1e-9


def test_outcome_record_flat_is_failure():
    rec = compute_outcome_record(2, "AAPL", _series([100, 101, 100, 102, 101, 100]))
    assert rec["label"] == 0


def test_outcome_record_too_short_returns_none():
    # SQUEEZE needs horizon+1 = 6 bars.
    assert compute_outcome_record(3, "X", _series([20, 21, 22])) is None


def test_build_library_separates_clusters():
    rng = np.random.default_rng(0)
    pos = pd.DataFrame(rng.normal(2, 0.2, (100, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)
    neg = pd.DataFrame(rng.normal(-2, 0.2, (100, len(FEATURE_COLUMNS))), columns=FEATURE_COLUMNS)
    df = pd.concat([pos, neg], ignore_index=True)
    labels = np.array([1] * 100 + [0] * 100)
    lib = build_library(df, labels)
    assert lib.query(np.full(len(FEATURE_COLUMNS), 2.0)) > 0.8


def test_synthetic_prices_have_business_day_index():
    s = _synthetic_closes("GME", datetime(2026, 1, 1, tzinfo=timezone.utc),
                          datetime(2026, 3, 1, tzinfo=timezone.utc))
    assert len(s) > 20
    assert (s > 0).all()
