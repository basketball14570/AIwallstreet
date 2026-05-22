"""Live inference — load the trained model and score a feature vector.

Lazy-loaded singleton so the model file is read once per process. Returns a
calibrated explosion probability the engine can use to *override* the rules
score once a trained model exists (champion/challenger). Falls back to None
when no model is on disk, so the rules engine keeps working day one.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pandas as pd

from app.ml.feature_engineering import FEATURE_COLUMNS, to_matrix
from app.ml.train import MODEL_PATH
from app.schemas.flow import FlowFeatureVector

_RELOAD_INTERVAL = 30.0


class ModelServer:
    _instance: "ModelServer | None" = None
    _path = MODEL_PATH
    _mtime: float | None = None
    _checked: float = 0.0

    def __init__(self, path: Path = MODEL_PATH):
        bundle = None
        if Path(path).exists():
            import joblib

            bundle = joblib.load(path)
        self.model = bundle["model"] if bundle else None
        self.features = bundle["features"] if bundle else FEATURE_COLUMNS

    @classmethod
    def get(cls) -> "ModelServer":
        """Singleton with mtime-based hot reload after a nightly retrain."""
        now = time.monotonic()
        if cls._instance is None or now - cls._checked >= _RELOAD_INTERVAL:
            cls._checked = now
            try:
                m = os.path.getmtime(cls._path)
            except OSError:
                m = None
            if cls._instance is None or m != cls._mtime:
                cls._mtime = m
                cls._instance = cls(cls._path)
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
