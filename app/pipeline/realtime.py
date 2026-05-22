"""Real-time event pipeline.

    FlowProvider.stream() ──► enrich (context) ──► track repeated sweeps
        ──► features ──► classify ──► persist ──► publish(redis) ──► alert

Runs as a single async task; scale horizontally by sharding providers/tickers
across workers (see README "Scaling").
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

from app.alerts.base import AlertDispatcher
from app.core.logging import get_logger
from app.core.redis_client import FLOW_CHANNEL, publish
from app.db.base import SessionLocal
from app.db.models import Alert, FlowFeatures, FlowScore, RawFlow
from app.providers.polygon import PolygonContextProvider
from app.providers.regime import RegimeProvider
from app.providers.unusual_whales import UnusualWhalesProvider
from app.schemas.flow import FlowEvent, ScoreResult
from app.scoring.classifier import classify

log = get_logger("pipeline")

SWEEP_WINDOW_SEC = 600  # 10 min lookback for repeated-sweep detection


class SweepTracker:
    """Counts recent sweeps per (ticker, contract) to detect repeated sweeps."""

    def __init__(self, window: int = SWEEP_WINDOW_SEC):
        self.window = window
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def record(self, event: FlowEvent) -> int:
        if not event.is_sweep:
            return 0
        key = f"{event.ticker}:{event.contract_type.value}:{event.strike}:{event.expiry:%Y%m%d}"
        now = time.time()
        dq = self._events[key]
        dq.append(now)
        while dq and now - dq[0] > self.window:
            dq.popleft()
        return len(dq)


class Pipeline:
    def __init__(self):
        self.provider = UnusualWhalesProvider()
        self.context = PolygonContextProvider()
        self.dispatcher = AlertDispatcher()
        self.sweeps = SweepTracker()
        self.regime_provider = RegimeProvider()

    async def _persist(self, event: FlowEvent, features, result: ScoreResult) -> int:
        async with SessionLocal() as session:
            raw = RawFlow(
                source=event.source,
                external_id=event.external_id,
                ticker=event.ticker,
                contract_type=event.contract_type.value,
                strike=event.strike,
                expiry=event.expiry,
                side=event.side.value if event.side else None,
                is_sweep=event.is_sweep,
                is_spread=event.is_spread,
                premium=event.premium,
                size=event.size,
                spot=event.spot,
                raw=event.raw,
                observed_at=event.observed_at,
            )
            session.add(raw)
            await session.flush()  # populate raw.id

            session.add(FlowFeatures(flow_id=raw.id, vector=features.model_dump(), **{
                k: getattr(features, k) for k in (
                    "ask_side_ratio", "sweep_urgency", "repeated_sweeps", "at_midpoint",
                    "otm_pct", "dte", "rel_options_volume", "stock_rvol", "oi_change_ratio",
                    "float_shares", "short_interest_pct", "borrow_rate", "dealer_gamma",
                    "social_score", "news_score", "historical_similarity",
                )
            }))
            session.add(FlowScore(
                flow_id=raw.id,
                classification=result.classification.value,
                confidence=result.confidence,
                explosion_prob=result.explosion_prob,
                squeeze_prob=result.squeeze_prob,
                momentum_prob=result.momentum_prob,
                fake_flow_prob=result.fake_flow_prob,
                component_scores=result.component_scores,
                reasons={"reasons": result.reasons},
                model_version=result.model_version,
            ))
            await session.commit()
            return raw.id

    async def _record_alert(self, flow_id: int, event: FlowEvent,
                            result: ScoreResult, channels: dict) -> None:
        from app.alerts.summary import generate_summary

        async with SessionLocal() as session:
            session.add(Alert(
                flow_id=flow_id,
                ticker=event.ticker,
                classification=result.classification.value,
                confidence=result.confidence,
                summary=generate_summary(event, result),
                channels=channels,
            ))
            await session.commit()

    async def process(self, event: FlowEvent) -> ScoreResult:
        ctx = await self.context.get_context(event.ticker)
        regime = await self.regime_provider.get_regime()
        repeated = self.sweeps.record(event)
        features, result = classify(event, ctx, repeated_sweeps=repeated, regime=regime)
        flow_id = await self._persist(event, features, result)

        await publish(FLOW_CHANNEL, {
            "flow_id": flow_id,
            "ticker": event.ticker,
            "classification": result.classification.value,
            "confidence": result.confidence,
            "explosion_prob": result.explosion_prob,
            "reasons": result.reasons,
        })

        if self.dispatcher.should_alert(result):
            channels = await self.dispatcher.dispatch(event, result)
            await self._record_alert(flow_id, event, result, channels)
            log.info("ALERT", ticker=event.ticker,
                     classification=result.classification.value,
                     confidence=result.confidence)
        return result

    async def run(self) -> None:
        log.info("pipeline starting")
        async for event in self.provider.stream():
            try:
                await self.process(event)
            except Exception as exc:  # noqa: BLE001
                log.error("process failed", ticker=event.ticker, error=str(exc))
