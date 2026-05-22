"""Ingest producer — provider stream -> durable Redis Stream.

Decoupling ingestion from scoring means a slow scorer (or a burst of flow)
can't drop events: the Stream absorbs the backpressure. Run one ingest process
per provider; run many scoring consumers.
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.core.queue import enqueue
from app.providers.unusual_whales import UnusualWhalesProvider

log = get_logger("ingest")


async def run_ingest() -> None:
    provider = UnusualWhalesProvider()
    log.info("ingest starting", provider=provider.name)
    async for event in provider.stream():
        await enqueue(event.model_dump())
