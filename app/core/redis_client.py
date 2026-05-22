"""Async Redis client + lightweight pub/sub helpers for the event pipeline."""
from __future__ import annotations

import json
from typing import Any, AsyncIterator

import redis.asyncio as redis

from app.config import settings

FLOW_CHANNEL = "flow.events"
ALERT_CHANNEL = "flow.alerts"

_pool: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _pool
    if _pool is None:
        _pool = redis.from_url(settings.redis_url, decode_responses=True)
    return _pool


async def publish(channel: str, payload: dict[str, Any]) -> None:
    await get_redis().publish(channel, json.dumps(payload, default=str))


async def subscribe(channel: str) -> AsyncIterator[dict[str, Any]]:
    pubsub = get_redis().pubsub()
    await pubsub.subscribe(channel)
    try:
        async for message in pubsub.listen():
            if message.get("type") == "message":
                yield json.loads(message["data"])
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
