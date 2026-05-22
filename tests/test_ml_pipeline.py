"""Tests for labelling, similarity, rate limiting, training and inference."""
from __future__ import annotations

import asyncio
import time

import numpy as np
import pandas as pd

from app.core.ratelimit import TokenBucket
from app.ml.labeling import BARRIERS, SetupType, label_event
from app.ml.train import train
from app.scoring.similarity import HistoricalLibrary
from app.schemas.flow import FlowFeatureVector


def test_success_definitions_match_spec():
    assert BARRIERS[SetupType.MOMENTUM].up == 0.10 and BARRIERS[SetupType.MOMENTUM].horizon == 2
    assert BARRIERS[SetupType.SQUEEZE].up == 0.20 and BARRIERS[SetupType.SQUEEZE].horizon == 5
    assert BARRIERS[SetupType.SCALPING].up == 0.03 and BARRIERS[SetupType.SCALPING].horizon == 1
    assert BARRIERS[SetupType.OPTIONS].up == 0.50


def test_triple_barrier_upper_then_label_one():
    # +12% on day 2 -> momentum success.
    path = pd.Series([100, 104, 112, 108])
    res = label_event(path, SetupType.MOMENTUM)
    assert res["label"] == 1 and res["touched"] == "upper"


def test_triple_barrier_stop_first_is_failure():
    # Craters -8% before any +10% -> labelled 0 even if it later recovers.
    path = pd.Series([100, 92, 130])
    res = label_event(path, SetupType.MOMENTUM)
    assert res["label"] == 0 and res["touched"] == "lower"


def test_historical_library_recovers_neighbourhood_hitrate():
    rng = np.random.default_rng(0)
    explosive = rng.normal(2.0, 0.3, (200, 16))
    benign = rng.normal(-2.0, 0.3, (200, 16))
    X = np.vstack([explosive, benign])
    y = np.array([1] * 200 + [0] * 200)
    lib = HistoricalLibrary.fit(X, y)
    assert lib.query(np.full(16, 2.0)) > 0.8   # near explosive cluster
    assert lib.query(np.full(16, -2.0)) < 0.2  # near benign cluster


def test_token_bucket_paces_calls():
    async def run():
        tb = TokenBucket(rate=10, capacity=2)  # 2 burst then 10/s
        t0 = time.monotonic()
        for _ in range(5):
            await tb.acquire()
        return time.monotonic() - t0
    elapsed = asyncio.run(run())
    # 2 free + 3 paced at 0.1s each ~ >= 0.25s
    assert elapsed >= 0.25


def test_train_and_inference_roundtrip(tmp_path, monkeypatch):
    rng = np.random.default_rng(1)
    from app.ml.feature_engineering import FEATURE_COLUMNS
    n = 800
    df = pd.DataFrame({c: rng.random(n) for c in FEATURE_COLUMNS})
    df["float_shares"] = rng.integers(5e6, 2e8, n)
    df["label"] = (df["sweep_urgency"] + df["short_interest_pct"] > 1.0).astype(int)

    import app.ml.train as tr
    monkeypatch.setattr(tr, "MODEL_PATH", tmp_path / "m.joblib")
    metrics = tr.train(df)
    assert 0.0 <= metrics["precision"] <= 1.0
    assert metrics["model"] in {"xgboost", "gbdt"}

    import joblib
    from app.ml.inference import ModelServer
    bundle = joblib.load(tmp_path / "m.joblib")
    server = ModelServer.__new__(ModelServer)
    server.model = bundle["model"]
    server.features = bundle["features"]
    p = server.predict_proba(FlowFeatureVector(sweep_urgency=0.9, short_interest_pct=0.9))
    assert 0.0 <= p <= 1.0
