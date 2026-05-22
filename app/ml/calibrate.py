"""Fit the confidence calibrator from realised outcomes.

Joins stored `flow_score.explosion_prob` to `outcome.label`, fits a Platt
calibrator, reports Expected Calibration Error before/after, and persists it.
The engine loads it via `Calibrator.load()`.

    python -m app.ml.calibrate            # demo on synthetic data
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

from app.scoring.calibration import Calibrator, expected_calibration_error

CALIBRATOR_PATH = Path("models/explosion_calibrator.json")


def fit_from_arrays(scores: list[float], labels: list[int],
                    path: Path = CALIBRATOR_PATH) -> dict:
    cal = Calibrator.fit_platt(scores, labels)
    cal.save(path)
    before = expected_calibration_error(scores, labels)
    after = expected_calibration_error([cal(s) for s in scores], labels)
    return {"a": round(cal.a, 4), "b": round(cal.b, 4),
            "ece_before": round(before, 4), "ece_after": round(after, 4),
            "n": len(scores), "path": str(path)}


async def fit_from_db(session) -> dict:
    """Join flow_score -> outcome and fit. Call from an async context."""
    from sqlalchemy import select

    from app.db.models import FlowScore, Outcome

    rows = (await session.execute(
        select(FlowScore.explosion_prob, Outcome.label)
        .join(Outcome, Outcome.flow_id == FlowScore.flow_id)
        .where(Outcome.label.isnot(None))
    )).all()
    if not rows:
        return {"error": "no labelled outcomes yet"}
    scores = [float(r[0]) for r in rows]
    labels = [int(r[1]) for r in rows]
    return fit_from_arrays(scores, labels)


if __name__ == "__main__":
    rng = np.random.default_rng(0)
    # Miscalibrated raw scores: model is over-confident at the extremes.
    raw = rng.beta(2, 2, 3000)
    true_p = raw ** 1.6
    labels = (rng.random(3000) < true_p).astype(int).tolist()
    print(fit_from_arrays(raw.tolist(), labels))
