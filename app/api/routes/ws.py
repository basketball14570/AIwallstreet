"""WebSocket endpoint streaming live scored flow to the dashboard.

Bridges the Redis flow channel to connected clients.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.logging import get_logger
from app.core.redis_client import FLOW_CHANNEL, subscribe

router = APIRouter(tags=["ws"])
log = get_logger("ws")


@router.websocket("/ws/flow")
async def flow_ws(websocket: WebSocket):
    await websocket.accept()
    try:
        async for payload in subscribe(FLOW_CHANNEL):
            await websocket.send_json(payload)
    except (WebSocketDisconnect, asyncio.CancelledError):
        log.info("ws client disconnected")
