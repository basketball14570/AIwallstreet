"""Async Redis client + lightweight pub/sub helpers for the event pipeline.

Redis is optional. When ``REDIS_URL`` is blank (or ``memory``/``none``) the app
falls back to an in-process broker + in-memory cache so the whole thing runs as a
single container with no external services — the "lite" / free-hosting path. The
public surface (``get_redis``, ``publish``, ``subscribe``) is identical either
way, so call sites never branch on it.
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from typing import Any, AsyncIterator

import redis.asyncio as redis

from app.config import settings

FLOW_CHANNEL = "flow.events"
ALERT_CHANNEL = "flow.alerts"

_pool: "redis.Redis | _MemoryRedis | None" = None


def _use_memory() -> bool:
    url = (settings.redis_url or "").strip().lower()
    return url in ("", "memory", "none", "disabled")


# --------------------------------------------------------------------------- #
# In-process fallback (no Redis)
# --------------------------------------------------------------------------- #
class _Broker:
    """Process-local fan-out: one set of asyncio queues per channel."""

    def __init__(self) -> None:
        self._subs: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def publish(self, channel: str, data: str) -> None:
        for q in list(self._subs.get(channel, ())):
            q.put_nowait(data)

    def add(self, channel: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs[channel].add(q)
        return q

    def remove(self, channel: str, q: asyncio.Queue) -> None:
        self._subs[channel].discard(q)


_broker = _Broker()


class _MemoryPubSub:
    """Mimics the slice of redis.asyncio.PubSub the app uses."""

    def __init__(self) -> None:
        self._q: asyncio.Queue | None = None
        self._channel: str | None = None

    async def subscribe(self, channel: str) -> None:
        self._channel = channel
        self._q = _broker.add(channel)

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "subscribe", "data": 1}
        assert self._q is not None
        while True:
            data = await self._q.get()
            yield {"type": "message", "data": data}

    async def unsubscribe(self, channel: str | None = None) -> None:
        if self._q is not None and self._channel is not None:
            _broker.remove(self._channel, self._q)

    async def close(self) -> None:
        await self.unsubscribe()


class _MemoryRedis:
    """In-memory stand-in implementing only what this app calls: string GET/SETEX
    (caching) and PUBLISH/PUBSUB (the live flow channel). Redis Streams (the
    horizontally-scaled worker path) are not supported here — lite mode runs the
    pipeline in-process instead."""

    def __init__(self) -> None:
        self._kv: dict[str, tuple[float, str]] = {}   # key -> (expiry_ts, value)

    async def get(self, key: str) -> str | None:
        item = self._kv.get(key)
        if not item:
            return None
        expiry, value = item
        if expiry and expiry < time.time():
            self._kv.pop(key, None)
            return None
        return value

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self._kv[key] = (time.time() + ttl, value)

    async def set(self, key: str, value: str) -> None:
        self._kv[key] = (0.0, value)

    async def publish(self, channel: str, data: str) -> None:
        _broker.publish(channel, data)

    def pubsub(self) -> _MemoryPubSub:
        return _MemoryPubSub()

    async def aclose(self) -> None:  # parity with redis.asyncio
        pass


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def get_redis() -> "redis.Redis | _MemoryRedis":
    global _pool
    if _pool is None:
        _pool = _MemoryRedis() if _use_memory() else redis.from_url(
            settings.redis_url, decode_responses=True)
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
