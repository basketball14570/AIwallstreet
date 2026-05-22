"""Standalone real-time pipeline worker.

    python -m app.worker
"""
from __future__ import annotations

import asyncio

from app.core.logging import configure_logging
from app.db.base import init_db
from app.pipeline.realtime import Pipeline


async def main() -> None:
    configure_logging()
    await init_db()
    await Pipeline().run()


if __name__ == "__main__":
    asyncio.run(main())
