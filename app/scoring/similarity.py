"""Historical similarity — 'how much does this look like past explosions?'

A k-NN over a library of labelled historical feature vectors. The returned
score is the *outcome-weighted neighbourhood hit-rate*: it answers "of the
setups most similar to this one, what fraction actually exploded?" — so it is
itself a (locally) calibrated probability, which is why the engine feeds it
straight into the explosion boost term.

Pure-numpy, no training step; rebuild nightly from the `outcome` table. For
millions of vectors swap the brute-force search for FAISS / hnswlib behind the
same `.query()` interface.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from app.ml.feature_engineering import FEATURE_COLUMNS
from app.schemas.flow import FlowFeatureVector


def vector_from_features(f: FlowFeatureVector) -> np.ndarray:
    """FEATURE_COLUMNS-ordered raw vector. Library z-normalises internally, so
    no scaling here — just None->0 and bool->float."""
    d = f.model_dump()
    return np.array([float(d.get(c) or 0.0) for c in FEATURE_COLUMNS], dtype=float)


class HistoricalLibrary:
    def __init__(self, X: np.ndarray, exploded: np.ndarray,
                 mean: np.ndarray, std: np.ndarray):
        self.X = X                 # z-normalised, L2-normalised vectors (N, D)
        self.exploded = exploded   # (N,) realised binary outcomes
        self.mean = mean
        self.std = std

    # ----- build ------------------------------------------------------------
    @classmethod
    def fit(cls, X_raw: np.ndarray, exploded: np.ndarray) -> "HistoricalLibrary":
        mean = X_raw.mean(axis=0)
        std = X_raw.std(axis=0) + 1e-9
        Z = (X_raw - mean) / std
        Z /= np.linalg.norm(Z, axis=1, keepdims=True) + 1e-9   # cosine via dot
        return cls(Z, exploded.astype(float), mean, std)

    # ----- query ------------------------------------------------------------
    def _prep(self, x_raw: np.ndarray) -> np.ndarray:
        z = (x_raw - self.mean) / self.std
        return z / (np.linalg.norm(z) + 1e-9)

    def query(self, x_raw: np.ndarray, k: int = 25) -> float:
        """Outcome-weighted hit-rate of the k nearest historical setups."""
        if len(self.X) == 0:
            return 0.0
        q = self._prep(x_raw)
        cos = self.X @ q                       # cosine similarity in [-1, 1]
        k = min(k, len(cos))
        idx = np.argpartition(-cos, k - 1)[:k]
        sim = np.clip(cos[idx], 0, 1)          # negatives carry no evidence
        w = sim / (sim.sum() + 1e-9)
        return float(np.dot(w, self.exploded[idx]))

    # ----- persistence ------------------------------------------------------
    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        np.savez(p, X=self.X, exploded=self.exploded, mean=self.mean, std=self.std)
        p.with_suffix(".meta.json").write_text(json.dumps({"features": FEATURE_COLUMNS}))

    @classmethod
    def load(cls, path: str | Path) -> "HistoricalLibrary | None":
        p = Path(path)
        if not p.exists():
            return None
        d = np.load(p)
        return cls(d["X"], d["exploded"], d["mean"], d["std"])
