"""Proactive AI scanner.

Ranks the day's most unusual opening setups, then has the AI analyst write up
only the top few — pushing them to Telegram/Discord unprompted. This is the
"automated analyst" loop: it surfaces a short, reasoned shortlist daily so you
don't have to pull up tickers yourself. You still do the trading.

Skipped entirely unless ANTHROPIC_API_KEY is set. Runs from the nightly job.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select

from app.alerts.discord import DiscordAlerter
from app.alerts.telegram import TelegramAlerter
from app.analysis.llm_analyst import ai_analyst_take
from app.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import FlowFeatures, RawFlow

log = get_logger("ai_scanner")


async def _top_tickers(limit: int, lookback_hours: int = 24) -> list[str]:
    """Distinct tickers behind the biggest unusual opening setups in the window."""
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    stmt = (
        select(RawFlow.ticker)
        .join(FlowFeatures, FlowFeatures.flow_id == RawFlow.id)
        .where(RawFlow.observed_at >= since)
        .where(FlowFeatures.is_opening.is_(True))
        .where(FlowFeatures.vol_oi >= settings.alert_unusual_vol_oi)
        .where(RawFlow.premium >= settings.alert_unusual_premium)
        .order_by(desc(RawFlow.premium))
        .limit(200)
    )
    async with SessionLocal() as session:
        rows = (await session.execute(stmt)).scalars().all()
    seen: list[str] = []
    for t in rows:
        if t not in seen:
            seen.append(t)
        if len(seen) >= limit:
            break
    return seen


async def run_ai_scan() -> dict:
    if not settings.anthropic_api_key:
        return {"sent": False, "reason": "no ANTHROPIC_API_KEY"}
    tickers = await _top_tickers(settings.ai_scan_top_n)
    if not tickers:
        log.info("ai scan: no qualifying setups")
        return {"sent": False, "reason": "no setups"}

    channels = [TelegramAlerter(), DiscordAlerter()]
    today = datetime.now(timezone.utc)
    header = (f"AI ANALYST — TOP {len(tickers)} SETUPS — {today:%b %d, %Y}\n"
              f"{', '.join(tickers)}\n(Educational, not financial advice.)")
    for ch in channels:
        try:
            await ch.send_text(header)
        except Exception as exc:  # noqa: BLE001
            log.error("ai scan header failed", channel=ch.name, error=str(exc))

    done = []
    for ticker in tickers:
        try:
            take = await ai_analyst_take(ticker)
        except Exception as exc:  # noqa: BLE001 — one bad ticker can't stop the scan
            log.error("ai take failed", ticker=ticker, error=str(exc))
            continue
        for ch in channels:
            try:
                await ch.send_text(take["take"])
            except Exception as exc:  # noqa: BLE001
                log.error("ai scan send failed", channel=ch.name, error=str(exc))
        done.append(ticker)
    log.info("ai scan sent", tickers=done)
    return {"sent": bool(done), "tickers": done}
