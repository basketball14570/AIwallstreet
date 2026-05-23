"""Daily 'top setups' digest.

Ranks the day's most unusual opening setups and sends a single compact summary
to the configured channels — a curated shortlist instead of a stream of pings,
which suits a beginner. Run from the nightly scheduler after the close.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select

from app.alerts.discord import DiscordAlerter
from app.alerts.telegram import TelegramAlerter
from app.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import FlowFeatures, RawFlow

log = get_logger("digest")


async def build_digest(limit: int = 5, lookback_hours: int = 24) -> str | None:
    """Top-N unusual opening setups in the window, deduped per contract. Returns
    a formatted message, or None if nothing qualified."""
    since = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
    stmt = (
        select(RawFlow, FlowFeatures)
        .join(FlowFeatures, FlowFeatures.flow_id == RawFlow.id)
        .where(RawFlow.observed_at >= since)
        .where(FlowFeatures.is_opening.is_(True))
        .where(FlowFeatures.vol_oi >= settings.alert_unusual_vol_oi)
        .where(RawFlow.premium >= settings.alert_unusual_premium)
        .order_by(desc(RawFlow.premium))
        .limit(200)
    )
    async with SessionLocal() as session:
        rows = (await session.execute(stmt)).all()

    seen: set[tuple] = set()
    picks: list[tuple] = []
    for raw, feats in rows:
        key = (raw.ticker, raw.contract_type, raw.strike, raw.expiry)
        if key in seen:
            continue
        seen.add(key)
        picks.append((raw, feats))
        if len(picks) >= limit:
            break

    if not picks:
        return None

    today = datetime.now(timezone.utc)
    lines = [f"DAILY TOP SETUPS — {today:%b %d, %Y}",
             f"The {len(picks)} most unusual opening trades today:\n"]
    for i, (raw, feats) in enumerate(picks, 1):
        lean = "bullish lean" if raw.contract_type == "call" else "bearish/hedge lean"
        lines.append(
            f"{i}. {raw.ticker}  ${raw.strike:g} {raw.contract_type} exp {raw.expiry:%b %d}\n"
            f"   ${raw.premium:,.0f} premium · {feats.vol_oi:.1f}x open interest · {lean}"
        )
    lines.append("\nOpen the dashboard's 'Analyze stock' tab for levels on any of "
                 "these. Educational information, not financial advice.")
    return "\n".join(lines)


async def send_digest() -> dict:
    """Build and broadcast the digest to configured channels."""
    msg = await build_digest()
    if msg is None:
        log.info("digest empty — no qualifying setups")
        return {"sent": False, "reason": "no setups"}
    status = {}
    for ch in (TelegramAlerter(), DiscordAlerter()):
        try:
            status[ch.name] = await ch.send_text(msg)
        except Exception as exc:  # noqa: BLE001
            status[ch.name] = "error"
            log.error("digest channel failed", channel=ch.name, error=str(exc))
    log.info("digest sent", **status)
    return {"sent": True, "channels": status}
