"""Watchlist price-alert monitor.

Polls every ticker on your watchlist and pings you when its price crosses the
breakout (resistance) or breakdown (support) level computed by the technical
analysis — the same '>= / <=' lines you'd draw on a chart. Runs as a background
task in the worker.
"""
from __future__ import annotations

import asyncio
import time

from sqlalchemy import select

from app.alerts.discord import DiscordAlerter
from app.alerts.telegram import TelegramAlerter
from app.analysis.technicals import analyze
from app.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import Watchlist
from app.providers.prices import PriceHistoryProvider

log = get_logger("watch_monitor")


class WatchlistMonitor:
    def __init__(self):
        self.prices = PriceHistoryProvider()
        self.channels = [TelegramAlerter(), DiscordAlerter()]
        self._last_price: dict[str, float] = {}
        self._last_alert: dict[str, float] = {}   # "TICKER:up"/"down" -> ts

    async def _tickers(self) -> list[str]:
        async with SessionLocal() as session:
            rows = (await session.execute(select(Watchlist))).scalars().all()
        return [w.ticker for w in rows]

    def _cooled(self, key: str) -> bool:
        last = self._last_alert.get(key)
        return last is not None and (time.monotonic() - last) < settings.alert_cooldown_min * 60.0

    async def _broadcast(self, text: str) -> None:
        for ch in self.channels:
            try:
                await ch.send_text(text)
            except Exception as exc:  # noqa: BLE001
                log.error("watch alert failed", channel=ch.name, error=str(exc))

    async def _check(self, ticker: str) -> None:
        price = await self.prices.current_price(ticker)
        if price is None:
            return
        prev = self._last_price.get(ticker)
        self._last_price[ticker] = price
        if prev is None:
            return  # establish baseline first; no alert on the first read

        ta = await analyze(ticker)
        if ta.get("data_source") == "unavailable":
            return
        up = ta.get("breakout_above")
        down = ta.get("breakdown_below")

        # Crossed UP through the breakout level.
        if up and prev < up <= price and not self._cooled(f"{ticker}:up"):
            self._last_alert[f"{ticker}:up"] = time.monotonic()
            await self._broadcast(
                f"PRICE ALERT — {ticker} broke out above ${up:,.2f}\n"
                f"Now ${price:,.2f}. Breaking a resistance ceiling is often a "
                f"bullish trigger. Next level up: ${ta.get('upside_target'):,.2f}.\n"
                "Educational information, not financial advice.")
        # Crossed DOWN through the breakdown level.
        elif down and prev > down >= price and not self._cooled(f"{ticker}:down"):
            self._last_alert[f"{ticker}:down"] = time.monotonic()
            await self._broadcast(
                f"PRICE ALERT — {ticker} broke down below ${down:,.2f}\n"
                f"Now ${price:,.2f}. Losing a support floor is often a bearish "
                "warning sign.\nEducational information, not financial advice.")

    async def run(self) -> None:
        log.info("watchlist monitor starting", interval=settings.watchlist_poll_sec)
        while True:
            try:
                for ticker in await self._tickers():
                    await self._check(ticker)
            except Exception as exc:  # noqa: BLE001 — never let the loop die
                log.error("watchlist monitor cycle failed", error=str(exc))
            await asyncio.sleep(settings.watchlist_poll_sec)
