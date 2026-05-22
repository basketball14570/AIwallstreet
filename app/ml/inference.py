"""Live inference — load the trained model and score a feature vector.

Lazy-loaded singleton so the model file is read once per process. Returns a
calibrated explosion probability the engine can use to *override* the rules
score once a trained model exists (champion/challenger). Falls back to None
when no model is on disk, so the rules engine keeps working day one.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from app.ml.feature_engineering import FEATURE_COLUMNS, to_matrix
from app.ml.train import MODEL_PATH
from app.schemas.flow import FlowFeatureVector


class ModelServer:
    _instance: "ModelServer | None" = None

    def __init__(self, path: Path = MODEL_PATH):
        bundle = None
        if Path(path).exists():
            import joblib

            bundle = joblib.load(path)
        self.model = bundle["model"] if bundle else None
        self.features = bundle["features"] if bundle else FEATURE_COLUMNS

    @classmethod
    def get(cls) -> "ModelServer":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @property
    def ready(self) -> bool:
        return self.model is not None

    def predict_proba(self, f: FlowFeatureVector) -> float | None:
        if self.model is None:
            return None
        row = pd.DataFrame([f.model_dump()]).reindex(columns=self.features)
        X = to_matrix(row)
        return float(self.model.predict_proba(X)[0, 1])


def predict(f: FlowFeatureVector) -> float | None:
    return ModelServer.get().predict_proba(f)
