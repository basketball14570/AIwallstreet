"""Telegram bot alerter."""
from __future__ import annotations

import httpx

from app.config import settings
from app.core.logging import get_logger
from app.schemas.flow import FlowEvent, ScoreResult

log = get_logger("alerts.telegram")


class TelegramAlerter:
    name = "telegram"

    async def send(self, summary: str, event: FlowEvent, result: ScoreResult) -> str:
        if not (settings.telegram_bot_token and settings.telegram_chat_id):
            log.info("telegram disabled", ticker=event.ticker)
            return "skipped"
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={
                    "chat_id": settings.telegram_chat_id,
                    "text": f"```\n{summary}\n```",
                    "parse_mode": "Markdown",
                },
            )
            resp.raise_for_status()
        return "sent"
