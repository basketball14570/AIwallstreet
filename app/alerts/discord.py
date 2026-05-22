"""Discord webhook alerter."""
from __future__ import annotations

import httpx

from app.config import settings
from app.core.logging import get_logger
from app.schemas.flow import FlowEvent, ScoreResult

log = get_logger("alerts.discord")


class DiscordAlerter:
    name = "discord"

    async def send(self, summary: str, event: FlowEvent, result: ScoreResult) -> str:
        if not settings.discord_webhook_url:
            log.info("discord disabled", ticker=event.ticker)
            return "skipped"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                settings.discord_webhook_url,
                json={"content": f"```\n{summary}\n```"},
            )
            resp.raise_for_status()
        return "sent"
