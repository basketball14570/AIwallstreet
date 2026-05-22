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
from app.schemas.flow import FlowEvent, FlowFeatureVector, ScoreResult
from app.scoring.classifier import classify
from app.scoring.sequence import SequenceTracker

log = get_logger("pipeline")

SWEEP_WINDOW_SEC = 600  # 10 min lookback for repeated-sweep detection


def flow_payload(flow_id: int, event: FlowEvent, result: ScoreResult,
                 features: "FlowFeatureVector | None" = None) -> dict:
    """Live-channel payload — carries contract details so the dashboard can
    show premium/size and roll prints up per contract, plus the screener
    signals (IV-rank, vol/OI, structure, follow-through)."""
    payload = {
        "flow_id": flow_id,
        "ticker": event.ticker,
        "contract_type": event.contract_type.value,
        "strike": event.strike,
        "expiry": event.expiry,
        "side": event.side.value if event.side else None,
        "premium": event.premium,
        "size": event.size,
        "is_sweep": event.is_sweep,
        "structure": event.structure.value,
        "iv": event.iv,
        "vol_oi": event.vol_oi,
        "spot": event.spot,
        "classification": result.classification.value,
        "confidence": result.confidence,
        "explosion_prob": result.explosion_prob,
        "reasons": result.reasons,
    }
    if features is not None:
        payload.update({
            "iv_rank": features.iv_rank,
            "is_opening": features.is_opening,
            "bullish_structure": features.bullish_structure >= 1.0,
            "follow_through": features.follow_through,
        })
    return payload


def _contract_key(event: FlowEvent) -> str:
    return f"{event.ticker}:{event.contract_type.value}:{event.strike}:{event.expiry:%Y%m%d}"


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


class SweepTracker:
    """Counts recent sweeps per (ticker, contract) to detect repeated sweeps."""

    def __init__(self, window: int = SWEEP_WINDOW_SEC):
        self.window = window
        self._events: dict[str, deque[float]] = defaultdict(deque)

    def record(self, event: FlowEvent) -> int:
        if not event.is_sweep:
            return 0
        key = _contract_key(event)
        now = time.time()
        dq = self._events[key]
        dq.append(now)
        while dq and now - dq[0] > self.window:
            dq.popleft()
        return len(dq)


class IVRankTracker:
    """Rolling per-ticker IV history -> IV-rank of the current print.

    IV-rank = (iv - min) / (max - min) over the lookback window, the standard
    'where does today's vol sit in its own recent range' measure. Returns
    ``None`` when the event carries no IV or there isn't enough history yet, so
    the feature layer falls back to context / neutral.
    """

    def __init__(self, window_sec: float = 30 * 86400.0, max_events: int = 500,
                 min_history: int = 8):
        self.window = window_sec
        self.min_history = min_history
        self._iv: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_events))

    def record(self, event: FlowEvent, now: float | None = None) -> float | None:
        if event.iv is None:
            return None
        now = now if now is not None else time.time()
        dq = self._iv[event.ticker]
        while dq and now - dq[0][0] > self.window:
            dq.popleft()
        prior = [iv for _, iv in dq]
        dq.append((now, float(event.iv)))
        if len(prior) < self.min_history:
            return None
        lo, hi = min(prior), max(prior)
        if hi <= lo:
            return 0.5
        return _clamp01((float(event.iv) - lo) / (hi - lo))


class FollowThroughTracker:
    """How much flow on *this exact contract* has already accumulated.

    A contract that prints repeatedly with growing premium is being added to —
    smart money following through on a thesis. ``record`` returns a 0-1 score
    derived from the prints that preceded this one, then folds the current print
    into the rolling window.
    """

    def __init__(self, window_sec: float = 3 * 86400.0, max_events: int = 500,
                 premium_target: float = 2_000_000.0):
        self.window = window_sec
        self.premium_target = premium_target
        self._events: dict[str, deque] = defaultdict(lambda: deque(maxlen=max_events))

    def record(self, event: FlowEvent, now: float | None = None) -> float:
        now = now if now is not None else time.time()
        dq = self._events[_contract_key(event)]
        while dq and now - dq[0][0] > self.window:
            dq.popleft()
        prior_count = len(dq)
        prior_premium = sum(p for _, p in dq)
        dq.append((now, float(event.premium)))
        if prior_count == 0:
            return 0.0
        from app.scoring.normalize import ramp
        by_count = ramp(prior_count, 1, 5)
        by_premium = ramp(prior_premium, 100_000, self.premium_target)
        return _clamp01(0.5 * by_count + 0.5 * by_premium)


class Pipeline:
    def __init__(self):
        self.provider = UnusualWhalesProvider()
        self.context = PolygonContextProvider()
        self.dispatcher = AlertDispatcher()
        self.sweeps = SweepTracker()
        self.sequences = SequenceTracker()
        self.iv_ranks = IVRankTracker()
        self.follow = FollowThroughTracker()
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
                    "iv_rank", "vol_oi", "is_opening", "days_to_earnings",
                    "bullish_structure", "follow_through", "ticker_hit_rate",
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
                regime=result.regime,
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
        seq = self.sequences.record(event)
        iv_rank = self.iv_ranks.record(event)
        follow = self.follow.record(event)
        features, result = classify(event, ctx, repeated_sweeps=repeated,
                                    regime=regime, seq=seq,
                                    follow_through=follow, iv_rank=iv_rank)
        flow_id = await self._persist(event, features, result)

        await publish(FLOW_CHANNEL, flow_payload(flow_id, event, result, features))

        if self.dispatcher.should_alert(result, event, features):
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
