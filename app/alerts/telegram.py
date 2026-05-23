"""Telegram bot alerter."""
from __future__ import annotations

import httpx

from app.alerts.links import journal_link
from app.config import settings
from app.core.logging import get_logger
from app.schemas.flow import FlowEvent, ScoreResult

log = get_logger("alerts.telegram")


class TelegramAlerter:
    name = "telegram"

    async def send(self, summary: str, event: FlowEvent, result: ScoreResult) -> str:
        # Card stays in a monospace code block; the journal link goes OUTSIDE it
        # so Telegram renders it as a tappable link.
        if not (settings.telegram_bot_token and settings.telegram_chat_id):
            return "skipped"
        url = journal_link(event)
        text = f"```\n{summary}\n```\n[➕ Add to journal]({url})"
        api = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                api,
                json={"chat_id": settings.telegram_chat_id, "text": text,
                      "parse_mode": "Markdown"},
            )
            resp.raise_for_status()
        return "sent"

    async def send_text(self, text: str,
                        links: list[tuple[str, str]] | None = None) -> str:
        """Send arbitrary text (used by the daily digest). Optional (label, url)
        links render as tappable links below the code block."""
        if not (settings.telegram_bot_token and settings.telegram_chat_id):
            return "skipped"
        body = f"```\n{text}\n```"
        if links:
            body += "\n" + "\n".join(f"[{lbl}]({url})" for lbl, url in links)
        url = f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                url,
                json={"chat_id": settings.telegram_chat_id, "text": body,
                      "parse_mode": "Markdown"},
            )
            resp.raise_for_status()
        return "sent"
