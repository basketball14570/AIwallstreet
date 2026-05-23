"""Backfills forward prices for journal entries.

For each saved idea we capture: an intraday snapshot ~N hours after the save,
the next session's open and close, and the close ~3 sessions later. Daily bars
only exist for trading days, so the date math naturally skips weekends/holidays.
Runs as a background task in the worker.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pandas as pd
from sqlalchemy import select

from app.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import JournalEntry
from app.providers.prices import PriceHistoryProvider

log = get_logger("journal_fill")


class JournalBackfiller:
    def __init__(self):
        self.prices = PriceHistoryProvider()

    async def _fill_one(self, e: JournalEntry, now: datetime) -> bool:
        """Fill any newly-available milestones. Returns True if anything changed."""
        changed = False
        created = e.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)
        entry_date = created.date()
        today = now.date()

        # Intraday snapshot, once we're past the N-hour mark.
        if (e.intraday_price is None
                and now >= created + timedelta(hours=settings.journal_intraday_hours)):
            price = await self.prices.current_price(e.ticker)
            if price is not None:
                e.intraday_price = price
                changed = True

        # Daily open/close milestones — only completed sessions (date < today).
        need_daily = (e.next_open_price is None or e.next_close_price is None
                      or e.day3_price is None)
        if need_daily:
            start = datetime.combine(entry_date - timedelta(days=4),
                                     datetime.min.time(), tzinfo=timezone.utc)
            bars = await self.prices.daily_bars(e.ticker, start, now)
            sessions = [(pd.Timestamp(idx).date(), row)
                        for idx, row in bars.iterrows()
                        if pd.Timestamp(idx).date() < today]

            after = [s for s in sessions if s[0] > entry_date]
            if after and (e.next_open_price is None or e.next_close_price is None):
                _, row = after[0]
                e.next_open_price = round(float(row["open"]), 2)
                e.next_close_price = round(float(row["close"]), 2)
                changed = True

            if e.day3_price is None:
                target = entry_date + timedelta(days=3)
                on_or_after = [s for s in sessions if s[0] >= target]
                if on_or_after:
                    e.day3_price = round(float(on_or_after[0][1]["close"]), 2)
                    changed = True
        return changed

    async def run_once(self) -> int:
        now = datetime.now(timezone.utc)
        async with SessionLocal() as session:
            rows = (await session.execute(
                select(JournalEntry).where(JournalEntry.day3_price.is_(None))
            )).scalars().all()
            updated = 0
            for e in rows:
                try:
                    if await self._fill_one(e, now):
                        updated += 1
                except Exception as exc:  # noqa: BLE001 — one bad ticker can't stop the rest
                    log.warning("journal fill failed", entry=e.id, error=str(exc))
            if updated:
                await session.commit()
        if updated:
            log.info("journal entries updated", count=updated)
        return updated

    async def run(self) -> None:
        log.info("journal backfiller starting", interval=settings.journal_poll_sec)
        while True:
            try:
                await self.run_once()
            except Exception as exc:  # noqa: BLE001 — never let the loop die
                log.error("journal backfiller cycle failed", error=str(exc))
            await asyncio.sleep(settings.journal_poll_sec)
