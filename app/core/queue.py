"""Durable event queue on Redis Streams.

Why Streams over plain pub/sub: pub/sub is fire-and-forget — a crashed consumer
loses messages. Streams persist events, support **consumer groups** (many
workers share a stream, each event delivered once), and track per-consumer
**pending entries** so an event a worker grabbed but never acked can be
re-claimed after a crash. That gives at-least-once delivery + horizontal scale.

    producer:  await enqueue(event_dict)
    consumer:  async for msg_id, data in consume(group, consumer): ... ; ack(...)
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

from redis.exceptions import ResponseError

from app.core.logging import get_logger
from app.core.redis_client import get_redis

log = get_logger("queue")

STREAM = "flow:ingest"
MAXLEN = 1_000_000   # approx-capped stream; old events trimmed


async def enqueue(event: dict[str, Any]) -> str:
    return await get_redis().xadd(
        STREAM, {"data": json.dumps(event, default=str)}, maxlen=MAXLEN, approximate=True
    )


async def ensure_group(group: str) -> None:
    try:
        await get_redis().xgroup_create(STREAM, group, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):   # group already exists -> fine
            raise


async def consume(group: str, consumer: str, block_ms: int = 5000,
                  batch: int = 50) -> AsyncIterator[tuple[str, dict]]:
    """Yield (message_id, event) for new entries. Caller must ack each id.

    On startup, first drains this consumer's pending (un-acked) backlog so a
    crash mid-batch never drops events.
    """
    r = get_redis()
    await ensure_group(group)
    backlog_id = "0"          # read own pending history first, then new (">")
    while True:
        streams = {STREAM: backlog_id}
        resp = await r.xreadgroup(group, consumer, streams, count=batch, block=block_ms)
        if not resp:
            backlog_id = ">"  # backlog drained -> switch to live
            continue
        for _stream, entries in resp:
            if not entries and backlog_id != ">":
                backlog_id = ">"
            for msg_id, fields in entries:
                yield msg_id, json.loads(fields["data"])
            if backlog_id != ">" and len(entries) < batch:
                backlog_id = ">"


async def ack(group: str, *msg_ids: str) -> None:
    if msg_ids:
        await get_redis().xack(STREAM, group, *msg_ids)


async def reclaim_stale(group: str, consumer: str, min_idle_ms: int = 60_000,
                        count: int = 100) -> list[tuple[str, dict]]:
    """Re-assign entries pending on dead consumers to this one (fault tolerance)."""
    r = get_redis()
    await ensure_group(group)   # XAUTOCLAIM needs the group to exist first
    _cursor, entries, _ = await r.xautoclaim(
        STREAM, group, consumer, min_idle_time=min_idle_ms, count=count
    )
    return [(mid, json.loads(f["data"])) for mid, f in entries]
