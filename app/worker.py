"""Pipeline worker entrypoint.

Modes (env WORKER_MODE):
  all      (default) — run ingest producer + one scoring consumer in-process
  ingest             — only the provider->stream producer
  consumer           — only a scoring consumer (scale these horizontally)

    python -m app.worker
"""
from __future__ import annotations

import asyncio
import os
import socket

from app.core.logging import configure_logging, get_logger
from app.db.base import init_db
from app.pipeline.consumer import ScoringConsumer
from app.pipeline.ingest import run_ingest

log = get_logger("worker")


async def main() -> None:
    configure_logging()
    await init_db()
    mode = os.getenv("WORKER_MODE", "all")
    name = f"{socket.gethostname()}-{os.getpid()}"

    tasks = []
    if mode in ("all", "ingest"):
        tasks.append(asyncio.create_task(run_ingest()))
    if mode in ("all", "consumer"):
        tasks.append(asyncio.create_task(ScoringConsumer(name).run()))
    log.info("worker started", mode=mode, name=name)
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
