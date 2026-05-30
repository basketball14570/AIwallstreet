"""FastAPI application entrypoint.

Boots the API and (optionally) the real-time pipeline as a background task.
Run API only:        uvicorn app.main:app --reload
Run pipeline worker: python -m app.worker
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import (
    alerts,
    analysis,
    flow,
    journal,
    levels,
    positions,
    screener,
    watchlist,
    ws,
)
from app.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.base import init_db

configure_logging()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    log.info("startup", env=settings.env)
    tasks: list = []
    # Single-container "lite" mode: run the real-time pipeline in-process so the
    # dashboard's live feed populates with no separate worker or Redis. The
    # in-memory bus (see core/redis_client) carries events to the WebSocket.
    if settings.run_pipeline_inprocess:
        import asyncio

        from app.pipeline.realtime import Pipeline
        tasks.append(asyncio.create_task(Pipeline().run()))
        log.info("in-process pipeline started")
    yield
    for t in tasks:
        t.cancel()
    log.info("shutdown")


app = FastAPI(title="AIwallstreet — Unusual Options Flow Intelligence",
              version="0.1.0", lifespan=lifespan)

app.include_router(flow.router)
app.include_router(watchlist.router)
app.include_router(ws.router)
app.include_router(alerts.router)
app.include_router(analysis.router)
app.include_router(journal.router)
app.include_router(levels.router)
app.include_router(positions.router)
app.include_router(screener.router)

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.env}


@app.get("/")
async def dashboard():
    return FileResponse(_STATIC / "index.html")
