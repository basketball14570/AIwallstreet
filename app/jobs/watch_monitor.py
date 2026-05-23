"""Watchlist price-alert monitor.

Polls every ticker on your watchlist and pings you when its price crosses a key
level — both the mechanical breakout/breakdown from the technical analysis AND
any trusted analyst support/resistance levels imported for the ticker. Runs as a
background task in the worker.
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
from app.db.models import AnalystLevels, Watchlist
from app.providers.prices import PriceHistoryProvider

log = get_logger("watch_monitor")


class WatchlistMonitor:
    def __init__(self):
        self.prices = PriceHistoryProvider()
        self.channels = [TelegramAlerter(), DiscordAlerter()]
        self._last_price: dict[str, float] = {}
        self._last_alert: dict[str, float] = {}   # "TICKER:level" -> ts

    async def _tickers(self) -> list[str]:
        async with SessionLocal() as session:
            rows = (await session.execute(select(Watchlist))).scalars().all()
        return [w.ticker for w in rows]

    async def _analyst(self, ticker: str) -> AnalystLevels | None:
        async with SessionLocal() as session:
            return await session.get(AnalystLevels, ticker.upper())

    def _cooled(self, key: str) -> bool:
        last = self._last_alert.get(key)
        return last is not None and (time.monotonic() - last) < settings.alert_cooldown_min * 60.0

    async def _broadcast(self, text: str) -> None:
        for ch in self.channels:
            try:
                await ch.send_text(text)
            except Exception as exc:  # noqa: BLE001
                log.error("watch alert failed", channel=ch.name, error=str(exc))

    async def _levels(self, ticker: str) -> list[tuple[str, float, str]]:
        """Collect (kind, value, label) levels to watch: mechanical chart levels
        plus trusted analyst levels. kind is 'resistance' or 'support'."""
        levels: list[tuple[str, float, str]] = []
        try:
            ta = await analyze(ticker)
        except Exception:  # noqa: BLE001
            ta = {"data_source": "unavailable"}
        if ta.get("data_source") != "unavailable":
            if ta.get("breakout_above"):
                levels.append(("resistance", ta["breakout_above"], "chart resistance"))
            if ta.get("breakdown_below"):
                levels.append(("support", ta["breakdown_below"], "chart support"))
        a = await self._analyst(ticker)
        if a:
            for r in (a.resistances or []):
                levels.append(("resistance", float(r), "analyst resistance"))
            for s in (a.supports or []):
                levels.append(("support", float(s), "analyst support"))
        return levels

    async def _check(self, ticker: str) -> None:
        price = await self.prices.current_price(ticker)
        if price is None:
            return
        prev = self._last_price.get(ticker)
        self._last_price[ticker] = price
        if prev is None:
            return  # establish baseline first; no alert on the first read

        approach = settings.watchlist_approach_pct / 100.0
        for kind, value, label in await self._levels(ticker):
            if value <= 0:
                continue
            key = f"{ticker}:{value:.2f}"
            ak = f"{key}:approach"
            # Resistance: alert when price crosses UP through it (breakout).
            if kind == "resistance" and prev < value <= price and not self._cooled(key):
                self._last_alert[key] = time.monotonic()
                await self._broadcast(
                    f"PRICE ALERT — {ticker} crossed ABOVE ${value:,.2f} ({label})\n"
                    f"Now ${price:,.2f}. Breaking a resistance ceiling is often a "
                    "bullish trigger.\nEducational information, not financial advice.")
            # Support: alert when price crosses DOWN through it (breakdown).
            elif kind == "support" and prev > value >= price and not self._cooled(key):
                self._last_alert[key] = time.monotonic()
                await self._broadcast(
                    f"PRICE ALERT — {ticker} crossed BELOW ${value:,.2f} ({label})\n"
                    f"Now ${price:,.2f}. Losing a support floor is often a bearish "
                    "warning sign.\nEducational information, not financial advice.")
            # Heads-up: just entered the approach band (within X%) without crossing.
            elif (abs(price - value) / value <= approach
                  and abs(prev - value) / value > approach
                  and not self._cooled(ak)):
                self._last_alert[ak] = time.monotonic()
                away = abs(price - value) / value * 100.0
                watch = ("a breakout or a rejection" if kind == "resistance"
                         else "a bounce or a breakdown")
                await self._broadcast(
                    f"HEADS UP — {ticker} approaching ${value:,.2f} ({label})\n"
                    f"Now ${price:,.2f} ({away:.1f}% away). Watch for {watch}.\n"
                    "Educational information, not financial advice.")

    async def run(self) -> None:
        log.info("watchlist monitor starting", interval=settings.watchlist_poll_sec)
        while True:
            try:
                for ticker in await self._tickers():
                    await self._check(ticker)
            except Exception as exc:  # noqa: BLE001 — never let the loop die
                log.error("watchlist monitor cycle failed", error=str(exc))
            await asyncio.sleep(settings.watchlist_poll_sec)
