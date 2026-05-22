"""Populate the `outcome` table from realised forward prices.

For every flow event old enough that its label horizon has elapsed and which
has no outcome yet, fetch forward daily closes and apply the triple-barrier
label. The canonical model target is the **squeeze** definition (+20% within 5
trading days) — that is what "explosion" means for the explosion classifier.

    python -m app.jobs.backfill            # offline demo via synthetic prices

This closes the data loop: live scoring -> stored flow -> realised outcomes ->
nightly retrain.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select

from app.core.logging import get_logger
from app.db.base import SessionLocal, init_db
from app.db.models import Outcome, RawFlow
from app.ml.labeling import BARRIERS, SetupType, triple_barrier_label
from app.providers.prices import PriceHistoryProvider

log = get_logger("backfill")

TARGET_SETUP = SetupType.SQUEEZE       # defines the explosion label
LOOKBACK_DAYS = 90                     # how far back to scan for unlabelled flow


def compute_outcome_record(flow_id: int, ticker: str, forward: pd.Series,
                           setup: SetupType = TARGET_SETUP) -> dict | None:
    """Build one Outcome row from a forward price series. None if too short."""
    barrier = BARRIERS[setup]
    if len(forward) < barrier.horizon + 1:
        return None
    entry = float(forward.iloc[0])
    rets = forward.to_numpy() / entry - 1.0

    def at(d: int) -> float | None:
        return float(rets[d]) if d < len(rets) else None

    tb = triple_barrier_label(forward, barrier)
    return {
        "flow_id": flow_id, "ticker": ticker,
        "ret_1d": at(1), "ret_3d": at(3), "ret_5d": at(5),
        "max_runup": tb["max_runup"], "max_drawdown": tb["max_drawdown"],
        "label": tb["label"],
    }


async def _unlabelled_events(session, since: datetime, horizon_days: int):
    """Events with no outcome whose horizon has fully elapsed."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=horizon_days + 1)
    stmt = (
        select(RawFlow.id, RawFlow.ticker, RawFlow.observed_at)
        .outerjoin(Outcome, Outcome.flow_id == RawFlow.id)
        .where(Outcome.flow_id.is_(None))
        .where(RawFlow.observed_at >= since)
        .where(RawFlow.observed_at <= cutoff)
    )
    return (await session.execute(stmt)).all()


async def backfill() -> dict:
    prices = PriceHistoryProvider()
    barrier = BARRIERS[TARGET_SETUP]
    since = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    async with SessionLocal() as session:
        events = await _unlabelled_events(session, since, barrier.horizon)
        if not events:
            return {"labelled": 0, "scanned": 0}

        # Fetch each ticker's forward window once.
        by_ticker: dict[str, list] = {}
        for fid, ticker, observed in events:
            by_ticker.setdefault(ticker, []).append((fid, observed))

        written = 0
        for ticker, rows in by_ticker.items():
            start = min(o for _, o in rows)
            end = max(o for _, o in rows) + timedelta(days=barrier.horizon * 3 + 7)
            series = await prices.daily_closes(ticker, start, end)
            if series.empty:
                continue
            for fid, observed in rows:
                fwd = series[series.index >= pd.Timestamp(observed).tz_localize(None).normalize()]
                rec = compute_outcome_record(fid, ticker, fwd)
                if rec is None:
                    continue
                session.add(Outcome(evaluated_at=datetime.now(timezone.utc), **rec))
                written += 1
        await session.commit()
        log.info("backfill complete", labelled=written, scanned=len(events))
        return {"labelled": written, "scanned": len(events)}


async def _main() -> None:
    await init_db()
    print(await backfill())


if __name__ == "__main__":
    asyncio.run(_main())
