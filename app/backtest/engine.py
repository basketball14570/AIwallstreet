"""Backtesting + historical replay.

Replays stored (or supplied) flow events through the *exact same* scoring code
the live pipeline uses, joins to realised outcomes, and reports the metrics the
spec asks for: hit rate, average move after alert, max drawdown, precision /
recall, and setup quality over time.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app.schemas.flow import FlowEvent, MarketContext
from app.scoring.classifier import classify


@dataclass
class BacktestConfig:
    alert_min_confidence: float = 70.0
    target_classifications: tuple[str, ...] = (
        "Strong Momentum Setup",
        "Potential Explosion Setup",
    )


def run_backtest(
    events: list[tuple[FlowEvent, MarketContext, dict]],
    config: BacktestConfig | None = None,
) -> dict:
    """`events` = list of (event, context, outcome) where outcome has keys
    ret_5d, max_runup, max_drawdown, label (1 = explosive)."""
    config = config or BacktestConfig()
    rows = []
    for event, ctx, outcome in events:
        _, result = classify(event, ctx)
        alerted = (
            result.confidence >= config.alert_min_confidence
            and result.classification.value in config.target_classifications
        )
        rows.append({
            "ticker": event.ticker,
            "observed_at": event.observed_at,
            "confidence": result.confidence,
            "explosion_prob": result.explosion_prob,
            "alerted": alerted,
            "label": outcome.get("label", 0),
            "ret_5d": outcome.get("ret_5d", np.nan),
            "max_runup": outcome.get("max_runup", np.nan),
            "max_drawdown": outcome.get("max_drawdown", np.nan),
        })
    df = pd.DataFrame(rows)
    return _metrics(df)


def _metrics(df: pd.DataFrame) -> dict:
    alerts = df[df["alerted"]]
    n_alerts = len(alerts)
    tp = int(((df["alerted"]) & (df["label"] == 1)).sum())
    fp = int(((df["alerted"]) & (df["label"] == 0)).sum())
    fn = int(((~df["alerted"]) & (df["label"] == 1)).sum())

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0

    return {
        "n_events": len(df),
        "n_alerts": n_alerts,
        "hit_rate": round(float(alerts["label"].mean()), 4) if n_alerts else 0.0,
        "avg_move_after_alert": round(float(alerts["ret_5d"].mean()), 4)
        if n_alerts else 0.0,
        "avg_max_runup": round(float(alerts["max_runup"].mean()), 4)
        if n_alerts else 0.0,
        "worst_drawdown": round(float(alerts["max_drawdown"].min()), 4)
        if n_alerts else 0.0,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        # TRUE NORTH: precision among only the high-conviction (>=90) alerts.
        # A trader needs a few elite setups, not many mediocre ones.
        "high_conf_precision": _precision_at(df, 90),
        "precision_by_confidence": _precision_by_bucket(df),
        "quality_over_time": _quality_over_time(alerts),
    }


def _precision_at(df: pd.DataFrame, threshold: float) -> dict:
    sel = df[df["confidence"] >= threshold]
    n = len(sel)
    return {
        "threshold": threshold,
        "n": n,
        "precision": round(float(sel["label"].mean()), 4) if n else None,
        "avg_move": round(float(sel["ret_5d"].mean()), 4) if n else None,
    }


def _precision_by_bucket(df: pd.DataFrame) -> dict:
    """Calibration view: realised hit-rate per confidence band. A well-behaved
    system shows monotonically rising precision with confidence."""
    bands = [(0, 50), (50, 70), (70, 90), (90, 101)]
    out = {}
    for lo, hi in bands:
        sel = df[(df["confidence"] >= lo) & (df["confidence"] < hi)]
        out[f"{lo}-{hi if hi <= 100 else 100}"] = {
            "n": len(sel),
            "precision": round(float(sel["label"].mean()), 4) if len(sel) else None,
        }
    return out


def _quality_over_time(alerts: pd.DataFrame) -> dict:
    if alerts.empty:
        return {}
    a = alerts.copy()
    ts = pd.to_datetime(a["observed_at"], utc=True).dt.tz_localize(None)
    a["week"] = ts.dt.to_period("W").astype(str)
    grp = a.groupby("week")["label"].mean().round(4)
    return grp.to_dict()
