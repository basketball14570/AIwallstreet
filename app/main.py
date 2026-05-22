"""FastAPI application entrypoint.

Boots the API and (optionally) the real-time pipeline as a background task.
Run API only:        uvicorn app.main:app --reload
Run pipeline worker: python -m app.worker
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import flow, watchlist, ws
from app.config import settings
from app.core.logging import configure_logging, get_logger
from app.db.base import init_db

configure_logging()
log = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    log.info("startup", env=settings.env)
    yield
    log.info("shutdown")


app = FastAPI(title="AIwallstreet — Unusual Options Flow Intelligence",
              version="0.1.0", lifespan=lifespan)

app.include_router(flow.router)
app.include_router(watchlist.router)
app.include_router(ws.router)


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.env}
