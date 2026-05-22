"""Async token-bucket rate limiter for outbound provider calls.

Providers (Unusual Whales, Polygon) enforce per-minute quotas. A token bucket
smooths bursts: up to `capacity` calls can fire immediately, then calls are
paced at `rate` tokens/sec. `acquire()` is awaitable and fair (FIFO via the
event loop), so concurrent workers share one budget without busy-waiting.
"""
from __future__ import annotations

import asyncio
import time


class TokenBucket:
    def __init__(self, rate: float, capacity: float | None = None):
        self.rate = rate
        self.capacity = capacity if capacity is not None else rate
        self._tokens = self.capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        self._tokens = min(self.capacity, self._tokens + (now - self._updated) * self.rate)
        self._updated = now

    async def acquire(self, n: float = 1.0) -> None:
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= n:
                    self._tokens -= n
                    return
                deficit = n - self._tokens
                wait = deficit / self.rate
            await asyncio.sleep(wait)
