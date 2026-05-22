"""Train + calibrate the explosion-probability classifier.

Pipeline:
  1. Load joined flow_features + outcome rows.
  2. Time-ordered split (no shuffling — avoids leakage across the event order).
  3. GradientBoosting base model wrapped in CalibratedClassifierCV (isotonic)
     so output probabilities are well calibrated (req: probability calibration).
  4. Report precision/recall/PR-AUC; persist model + metadata via joblib.

Recommended model progression:
  MVP        -> GradientBoostingClassifier (handles non-linear interactions,
                small data, no scaling needed).
  Scale-up   -> LightGBM / XGBoost with monotonic constraints on squeeze fuel.
  Advanced   -> two-stage: fake-flow filter, then explosion ranker.

False-positive reduction: optimise for precision at a fixed alert budget
(top-k per day) rather than raw accuracy; class_weight balances rarity.
"""
from __future__ import annotations

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import average_precision_score, precision_recall_fscore_support

from app.ml.feature_engineering import FEATURE_COLUMNS, to_matrix

MODEL_PATH = Path("models/explosion_clf.joblib")


def train(df: pd.DataFrame, test_frac: float = 0.2) -> dict:
    """`df` must contain FEATURE_COLUMNS + a binary `label` column, ordered by time."""
    df = df.dropna(subset=["label"]).reset_index(drop=True)
    split = int(len(df) * (1 - test_frac))
    train_df, test_df = df.iloc[:split], df.iloc[split:]

    X_train, y_train = to_matrix(train_df), train_df["label"].to_numpy()
    X_test, y_test = to_matrix(test_df), test_df["label"].to_numpy()

    base = GradientBoostingClassifier(random_state=42)
    model = CalibratedClassifierCV(base, method="isotonic", cv=3)
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    preds = (proba >= 0.5).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_test, preds, average="binary", zero_division=0
    )
    metrics = {
        "n_train": len(train_df),
        "n_test": len(test_df),
        "precision": round(float(precision), 4),
        "recall": round(float(recall), 4),
        "f1": round(float(f1), 4),
        "pr_auc": round(float(average_precision_score(y_test, proba)), 4)
        if y_test.sum() else None,
        "positive_rate": round(float(np.mean(y_test)), 4),
    }

    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"model": model, "features": FEATURE_COLUMNS, "metrics": metrics},
                MODEL_PATH)
    return metrics


if __name__ == "__main__":
    # Demo on synthetic data so the script runs without a populated DB.
    rng = np.random.default_rng(0)
    n = 2000
    demo = pd.DataFrame({c: rng.random(n) for c in FEATURE_COLUMNS})
    demo["float_shares"] = rng.integers(5e6, 2e8, n)
    signal = demo["sweep_urgency"] + demo["short_interest_pct"] + demo["rel_options_volume"]
    demo["label"] = (signal + rng.normal(0, 0.5, n) > 2.0).astype(int)
    print(train(demo))
