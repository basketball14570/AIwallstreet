"""Tests for intra-event sequence tracking + temporal assembly."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from app.ml.feature_engineering import FEATURE_COLUMNS
from app.ml.sequences import build_sequences, to_padded_tensor
from app.schemas.flow import ContractType, FlowEvent, FlowFeatureVector, Side
from app.scoring.classifier import classify_features
from app.scoring.sequence import SequenceTracker


def _ev(ticker="GME", strike=22.0, premium=500_000, ctype=ContractType.CALL) -> FlowEvent:
    return FlowEvent(
        source="t", ticker=ticker, contract_type=ctype, strike=strike,
        expiry=datetime.now(timezone.utc) + timedelta(days=10), side=Side.ASK,
        is_sweep=True, premium=premium, size=1000, spot=20.0,
        observed_at=datetime.now(timezone.utc),
    )


def test_cadence_acceleration_detected():
    tr = SequenceTracker()
    feats = None
    for t in (0, 10, 20, 25, 28):           # gaps shrinking 10,10,5,3
        feats = tr.record(_ev(), now=float(t))
    assert feats.cadence_accel > 0.3


def test_constant_cadence_is_flat():
    tr = SequenceTracker()
    feats = None
    for t in (0, 10, 20, 30, 40):           # constant gaps
        feats = tr.record(_ev(), now=float(t))
    assert feats.cadence_accel < 0.05


def test_strike_laddering_up():
    tr = SequenceTracker()
    feats = None
    for i, strike in enumerate([20, 21, 22, 23]):
        feats = tr.record(_ev(strike=strike), now=float(i))
    assert feats.strike_ladder == 1.0
    assert feats.premium_velocity > 0


def test_sequence_features_lift_conviction():
    base = FlowFeatureVector(ask_side_ratio=1.0, sweep_urgency=0.5)
    fast = FlowFeatureVector(ask_side_ratio=1.0, sweep_urgency=0.5,
                             seq_cadence_accel=0.8, seq_strike_ladder=0.8)
    b = classify_features(base).component_scores["conviction"]
    f = classify_features(fast).component_scores["conviction"]
    assert f > b


def test_build_and_pad_sequences():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    rows = []
    for tkr, n in (("GME", 5), ("AMC", 60)):
        for i in range(n):
            r = {c: 0.0 for c in FEATURE_COLUMNS}
            r.update(ticker=tkr, observed_at=now + timedelta(minutes=i),
                     sweep_urgency=float(i))
            rows.append(r)
    df = pd.DataFrame(rows)
    seqs = build_sequences(df, max_len=50)
    assert {k[0] for k, _ in seqs} == {"GME", "AMC"}
    X, mask, keys = to_padded_tensor(seqs, max_len=50)
    assert X.shape == (2, 50, len(FEATURE_COLUMNS))
    # AMC truncated to last 50; GME right-aligned with 45 padded rows.
    gme_idx = keys.index(("GME",))
    assert mask[gme_idx].sum() == 5
    assert not mask[gme_idx, 0]              # padded at the front
    assert mask[gme_idx, -1]                 # real at the end
