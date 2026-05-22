"""Alert dispatch — fans a setup out to all configured channels."""
from __future__ import annotations

import asyncio

from app.alerts.discord import DiscordAlerter
from app.alerts.summary import generate_summary
from app.alerts.telegram import TelegramAlerter
from app.config import settings
from app.core.logging import get_logger
from app.schemas.flow import FlowEvent, ScoreResult

log = get_logger("alerts")


class AlertDispatcher:
    def __init__(self):
        self.channels = [DiscordAlerter(), TelegramAlerter()]

    def should_alert(self, result: ScoreResult) -> bool:
        return result.confidence >= settings.alert_min_confidence

    async def dispatch(self, event: FlowEvent, result: ScoreResult) -> dict[str, str]:
        summary = generate_summary(event, result)
        results = await asyncio.gather(
            *(ch.send(summary, event, result) for ch in self.channels),
            return_exceptions=True,
        )
        status = {}
        for ch, res in zip(self.channels, results):
            status[ch.name] = "error" if isinstance(res, Exception) else str(res)
            if isinstance(res, Exception):
                log.error("alert channel failed", channel=ch.name, error=str(res))
        return status
