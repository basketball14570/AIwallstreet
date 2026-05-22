"""Nightly retrain — rebuild model, calibrator and similarity library.

Consumes the labelled `outcome` rows produced by backfill:
  1. flow_features ⋈ outcome  -> train explosion classifier (ml/train).
  2. same matrix + labels      -> rebuild historical k-NN library.
  3. flow_score.explosion_prob ⋈ outcome.label -> fit confidence calibrator.

All artifacts land in ``models/`` where the live engine auto-loads them, so the
next scored event uses the fresh model with zero downtime.
"""
from __future__ import annotations

import asyncio

import numpy as np
import pandas as pd
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.base import SessionLocal, init_db
from app.db.models import FlowFeatures, FlowScore, Outcome
from app.ml.calibrate import fit_from_arrays
from app.ml.feature_engineering import FEATURE_COLUMNS
from app.ml.train import train
from app.scoring.similarity import HistoricalLibrary

log = get_logger("retrain")

LIBRARY_PATH = "models/historical_library.npz"
MIN_ROWS = 200          # don't (re)train on too little data
MIN_POSITIVES = 20


def build_library(feat_df: pd.DataFrame, labels: np.ndarray) -> HistoricalLibrary:
    X = feat_df.reindex(columns=FEATURE_COLUMNS).fillna(0.0).to_numpy(dtype=float)
    return HistoricalLibrary.fit(X, labels)


async def _load_training_frame(session) -> pd.DataFrame:
    cols = [getattr(FlowFeatures, c) for c in FEATURE_COLUMNS]
    stmt = (select(FlowFeatures.flow_id, *cols, Outcome.label)
            .join(Outcome, Outcome.flow_id == FlowFeatures.flow_id)
            .where(Outcome.label.isnot(None))
            .order_by(FlowFeatures.flow_id))
    rows = (await session.execute(stmt)).all()
    return pd.DataFrame(rows, columns=["flow_id", *FEATURE_COLUMNS, "label"])


async def _load_calibration_pairs(session) -> tuple[list[float], list[int]]:
    rows = (await session.execute(
        select(FlowScore.explosion_prob, Outcome.label)
        .join(Outcome, Outcome.flow_id == FlowScore.flow_id)
        .where(Outcome.label.isnot(None))
    )).all()
    return [float(r[0]) for r in rows], [int(r[1]) for r in rows]


async def retrain() -> dict:
    async with SessionLocal() as session:
        df = await _load_training_frame(session)
        if len(df) < MIN_ROWS or int(df["label"].sum()) < MIN_POSITIVES:
            return {"skipped": True, "rows": len(df),
                    "positives": int(df["label"].sum()) if len(df) else 0}

        metrics = train(df.drop(columns=["flow_id"]))

        lib = build_library(df, df["label"].to_numpy())
        lib.save(LIBRARY_PATH)

        scores, labels = await _load_calibration_pairs(session)
        cal = fit_from_arrays(scores, labels) if scores else {"skipped": "no scores"}

        log.info("retrain complete", **metrics)
        return {"model": metrics, "calibration": cal,
                "library_size": int(len(lib.X))}


async def _main() -> None:
    await init_db()
    print(await retrain())


if __name__ == "__main__":
    asyncio.run(_main())
