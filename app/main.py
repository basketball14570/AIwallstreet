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

from app.api.routes import alerts, analysis, flow, watchlist, ws
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
app.include_router(alerts.router)
app.include_router(analysis.router)

_STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=_STATIC), name="static")


@app.get("/health")
async def health():
    return {"status": "ok", "env": settings.env}


@app.get("/")
async def dashboard():
    return FileResponse(_STATIC / "index.html")
