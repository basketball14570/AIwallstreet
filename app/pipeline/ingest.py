"""Ingest producer — provider stream -> durable Redis Stream.

Decoupling ingestion from scoring means a slow scorer (or a burst of flow)
can't drop events: the Stream absorbs the backpressure. Run one ingest process
per provider; run many scoring consumers.
"""
from __future__ import annotations

from app.config import settings
from app.core.logging import get_logger
from app.core.queue import enqueue
from app.providers.base import FlowProvider
from app.providers.polygon_flow import PolygonFlowProvider
from app.providers.unusual_whales import UnusualWhalesProvider

log = get_logger("ingest")


def _select_provider() -> FlowProvider:
    """Pick the flow source from config. 'auto' uses Polygon when a key is set
    (real options data on paid plans), otherwise the synthetic feed."""
    choice = settings.flow_provider.lower()
    if choice == "unusual_whales":
        return UnusualWhalesProvider()
    if choice == "polygon":
        return PolygonFlowProvider()
    if choice == "auto" and settings.polygon_api_key:
        return PolygonFlowProvider()
    return UnusualWhalesProvider()   # default / 'synthetic' (UW falls back to synthetic)


async def run_ingest() -> None:
    provider = _select_provider()
    log.info("ingest starting", provider=provider.name)
    async for event in provider.stream():
        await enqueue(event.model_dump())
