"""Train + calibrate the explosion classifier.

Model selection
---------------
* MVP / default: GradientBoostingClassifier — robust on small, noisy, tabular
  data, no scaling, captures non-linear interactions out of the box.
* Preferred at scale: XGBoost with **monotonic constraints** (used automatically
  if `xgboost` is installed). Constraints encode domain priors — e.g. explosion
  probability must be non-decreasing in short-interest, RVOL, sweep urgency, and
  non-increasing in dealer gamma. This is a strong regulariser that *reduces
  overfitting and false positives* by forbidding the model from learning
  spurious non-monotone wiggles in sparse regions.

XGBoost vs neural networks: for ~10^4-10^6 tabular rows with heterogeneous,
partly-missing features, gradient-boosted trees beat NNs on accuracy, training
cost, and calibration, and give free feature importances. Reach for a NN (or a
sequence model / Temporal CNN over the order-flow time series) only once you
model raw tick/flow *sequences* rather than aggregated features.

Overfitting controls: time-ordered split (no shuffling), early-stopping-style
shallow trees, monotone constraints, isotonic calibration on a held-out fold,
and reporting PR-AUC (not accuracy) because positives are rare.
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

# +1 = explosion prob must rise with the feature, -1 = must fall, 0 = free.
MONOTONE = {
    "ask_side_ratio": 1, "sweep_urgency": 1, "repeated_sweeps": 1,
    "rel_options_volume": 1, "stock_rvol": 1, "oi_change_ratio": 1,
    "short_interest_pct": 1, "borrow_rate": 1, "social_score": 1,
    "news_score": 1, "historical_similarity": 1, "dealer_gamma": -1,
}


def _build_estimator():
    try:
        from xgboost import XGBClassifier

        constraints = tuple(MONOTONE.get(c, 0) for c in FEATURE_COLUMNS)
        return XGBClassifier(
            n_estimators=400, max_depth=4, learning_rate=0.03,
            subsample=0.8, colsample_bytree=0.8, reg_lambda=2.0,
            monotone_constraints=constraints, eval_metric="aucpr",
            tree_method="hist", n_jobs=-1,
        ), "xgboost"
    except ImportError:
        return GradientBoostingClassifier(
            n_estimators=300, max_depth=3, learning_rate=0.05, subsample=0.8,
            random_state=42,
        ), "gbdt"


def train(df: pd.DataFrame, test_frac: float = 0.2) -> dict:
    """`df` must contain FEATURE_COLUMNS + a binary `label`, ordered by time."""
    df = df.dropna(subset=["label"]).reset_index(drop=True)
    split = int(len(df) * (1 - test_frac))
    train_df, test_df = df.iloc[:split], df.iloc[split:]

    X_train, y_train = to_matrix(train_df), train_df["label"].to_numpy()
    X_test, y_test = to_matrix(test_df), test_df["label"].to_numpy()

    base, kind = _build_estimator()
    model = CalibratedClassifierCV(base, method="isotonic", cv=3)
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    preds = (proba >= 0.5).astype(int)
    precision, recall, f1, _ = precision_recall_fscore_support(
        y_test, preds, average="binary", zero_division=0
    )
    metrics = {
        "model": kind, "n_train": len(train_df), "n_test": len(test_df),
        "precision": round(float(precision), 4), "recall": round(float(recall), 4),
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
    rng = np.random.default_rng(0)
    n = 2000
    demo = pd.DataFrame({c: rng.random(n) for c in FEATURE_COLUMNS})
    demo["float_shares"] = rng.integers(5e6, 2e8, n)
    signal = demo["sweep_urgency"] + demo["short_interest_pct"] + demo["rel_options_volume"]
    demo["label"] = (signal + rng.normal(0, 0.5, n) > 2.0).astype(int)
    print(train(demo))
