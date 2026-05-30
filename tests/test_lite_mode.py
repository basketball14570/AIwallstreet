"""Tests for the zero-infra 'lite' path: in-memory event bus (no Redis) and the
SQLite-compatible schema (no Postgres) that make single-container free hosting
work. These exercise the fallbacks directly without any external services.
"""
from __future__ import annotations

import asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine


# ----- in-memory pub/sub bus ------------------------------------------------
def test_memory_bus_publish_subscribe_roundtrip(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "redis_url", "")   # force the in-memory bus

    import app.core.redis_client as rc
    monkeypatch.setattr(rc, "_pool", None)            # rebuild the client
    assert isinstance(rc.get_redis(), rc._MemoryRedis)

    async def scenario():
        sub = rc.subscribe(rc.FLOW_CHANNEL)
        agen = sub.__aiter__()
        # Start consuming first so the subscriber's queue is registered before we
        # publish (registration happens on the generator's first step).
        fut = asyncio.ensure_future(agen.__anext__())
        await asyncio.sleep(0.05)
        await rc.publish(rc.FLOW_CHANNEL, {"ticker": "GME", "n": 1})
        msg = await asyncio.wait_for(fut, timeout=1.0)
        await agen.aclose()
        return msg

    out = asyncio.run(scenario())
    assert out == {"ticker": "GME", "n": 1}


def test_memory_redis_cache_get_setex_and_expiry():
    import app.core.redis_client as rc
    r = rc._MemoryRedis()

    async def scenario():
        assert await r.get("missing") is None
        await r.setex("k", 60, "v")
        assert await r.get("k") == "v"
        # An already-expired key reads back as a miss.
        await r.setex("old", -1, "stale")
        assert await r.get("old") is None

    asyncio.run(scenario())


# ----- SQLite-compatible schema --------------------------------------------
def test_sqlite_schema_roundtrip_json_and_autoincrement():
    """create_all + insert a RawFlow on SQLite: JSONType stores a dict and the
    BigIntPK primary key auto-generates (the bug that BIGINT doesn't on SQLite)."""
    from datetime import datetime, timezone

    from app.db.base import Base
    from app.db.models import RawFlow

    async def scenario():
        engine = create_async_engine("sqlite+aiosqlite://")  # in-memory
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)
        async with Session() as s:
            row = RawFlow(
                source="test", ticker="GME", contract_type="call", strike=20.0,
                expiry=datetime(2026, 7, 1, tzinfo=timezone.utc), side="ask",
                is_sweep=True, premium=500_000, size=1000, spot=18.0,
                raw={"a": 1, "b": [2, 3]},
                observed_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
            )
            s.add(row)
            await s.commit()
            assert row.id is not None and row.id >= 1     # autoincrement worked
            got = (await s.execute(select(RawFlow))).scalar_one()
            assert got.raw == {"a": 1, "b": [2, 3]}       # JSON roundtripped
        await engine.dispose()

    asyncio.run(scenario())
