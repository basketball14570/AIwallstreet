"""Nightly maintenance orchestrator: backfill outcomes, then retrain.

    python -m app.jobs.nightly                 # run once (use with cron/k8s CronJob)
    python -m app.jobs.nightly --loop 86400    # run every N seconds (in-process)

Cron is preferred in production; the loop mode is a convenience for single-box
deployments.
"""
from __future__ import annotations

import argparse
import asyncio

from app.core.logging import configure_logging, get_logger
from app.db.base import init_db
from app.jobs.backfill import backfill
from app.jobs.digest import send_digest
from app.jobs.retrain import retrain

log = get_logger("nightly")


async def run_once() -> dict:
    bf = await backfill()
    rt = await retrain()
    try:
        dg = await send_digest()
    except Exception as exc:  # noqa: BLE001 — digest must never break maintenance
        log.error("digest failed", error=str(exc))
        dg = {"sent": False, "error": str(exc)}
    result = {"backfill": bf, "retrain": rt, "digest": dg}
    log.info("nightly done", **{k: str(v) for k, v in result.items()})
    return result


async def _main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", type=int, default=0,
                        help="seconds between runs; 0 = run once and exit")
    args = parser.parse_args()

    await init_db()
    if args.loop <= 0:
        print(await run_once())
        return
    while True:
        try:
            await run_once()
        except Exception as exc:  # noqa: BLE001 — never let the scheduler die
            log.error("nightly run failed", error=str(exc))
        await asyncio.sleep(args.loop)


if __name__ == "__main__":
    asyncio.run(_main())
