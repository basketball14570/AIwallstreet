"""Proactive technical breakout scan.

Sweeps the configured universe with the self-driven technical screener, then
cross-references it against the unusual options flow the pipeline has already
captured. The highest-conviction "about to explode" candidate is **confluence**:
a stock mechanically coiling under resistance *and* showing fresh unusual call
buying. Those are boosted and flagged.

Top candidates above the score threshold are pushed to Telegram/Discord. Runs
on demand (the ``/screener`` endpoint shares the same code) and, when
``SCREENER_SCAN_ENABLED`` is set, from the nightly job.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, func, select

from app.alerts.discord import DiscordAlerter
from app.alerts.telegram import TelegramAlerter
from app.analysis.screener import scan_universe
from app.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import FlowFeatures, RawFlow

log = get_logger("screener_scan")

# Confluence adds up to this many points to the composite when a coiling name is
# also seeing fresh unusual call flow — capped so flow can't fully override the
# technical read.
_CONFLUENCE_BOOST = 8.0


def _universe() -> list[str]:
    return [t.strip().upper() for t in settings.screener_universe.split(",") if t.strip()]


async def _unusual_call_flow() -> dict[str, float]:
    """Map of ticker -> largest recent unusual opening CALL premium, for tickers
    with fresh bullish flow. Empty when the flow tables aren't populated yet."""
    since = datetime.now(timezone.utc) - timedelta(hours=settings.screener_flow_lookback_hours)
    stmt = (
        select(RawFlow.ticker, func.max(RawFlow.premium))
        .join(FlowFeatures, FlowFeatures.flow_id == RawFlow.id)
        .where(RawFlow.observed_at >= since)
        .where(RawFlow.contract_type == "call")
        .where(FlowFeatures.is_opening.is_(True))
        .where(FlowFeatures.vol_oi >= settings.alert_unusual_vol_oi)
        .where(RawFlow.premium >= settings.alert_unusual_premium)
        .group_by(RawFlow.ticker)
    )
    try:
        async with SessionLocal() as session:
            rows = (await session.execute(stmt)).all()
    except Exception as exc:  # noqa: BLE001 — flow DB is optional for the screener
        log.warning("confluence flow query failed", error=str(exc))
        return {}
    return {t.upper(): float(p or 0) for t, p in rows}


async def run_screen(top_n: int | None = None, min_score: float | None = None,
                     include_flow: bool = True) -> dict:
    """Run the screener over the universe, annotate with flow confluence, and
    return the ranked candidates. Does not send alerts — that's ``run_scan``."""
    top_n = top_n if top_n is not None else settings.screener_top_n
    min_score = min_score if min_score is not None else settings.screener_min_score

    setups = await scan_universe(_universe())
    flow = await _unusual_call_flow() if include_flow else {}
    for s in setups:
        prem = flow.get(s["ticker"])
        s["unusual_call_flow"] = prem is not None
        s["flow_premium"] = round(prem, 0) if prem else None
        if prem is not None:
            s["setup_score"] = round(min(100.0, s["setup_score"] + _CONFLUENCE_BOOST), 1)
            s["reasons"] = (["CONFLUENCE — also seeing fresh unusual call buying "
                             f"(${prem/1e6:.1f}M premium): technicals + flow agree"]
                            + s["reasons"])
    setups.sort(key=lambda s: (s["unusual_call_flow"], s["setup_score"]), reverse=True)
    qualifying = [s for s in setups if s["setup_score"] >= min_score]
    return {"candidates": qualifying[:top_n], "scanned": len(setups),
            "min_score": min_score}


def _format(s: dict) -> str:
    arrow = "🔥 " if s.get("unusual_call_flow") else ""
    lines = [
        f"{arrow}{s['ticker']} — {s['classification']} (score {s['setup_score']:.0f}/100)",
        f"  Spot ${s['spot']:,.2f}",
    ]
    if s.get("breakout_trigger"):
        lines.append(f"  Break above ${s['breakout_trigger']:,.2f} → target "
                     f"${s['target']:,.2f}" if s.get("target")
                     else f"  Break above ${s['breakout_trigger']:,.2f}")
    if s.get("stop"):
        lines.append(f"  Thesis fails below ${s['stop']:,.2f}")
    for r in s["reasons"][:3]:
        lines.append(f"  • {r}")
    return "\n".join(lines)


async def run_scan() -> dict:
    """Nightly entrypoint: screen, then push the shortlist to alert channels."""
    if not settings.screener_scan_enabled:
        return {"sent": False, "reason": "screener scan disabled (SCREENER_SCAN_ENABLED=false)"}
    result = await run_screen()
    candidates = result["candidates"]
    if not candidates:
        log.info("screener scan: no qualifying setups", scanned=result["scanned"])
        return {"sent": False, "reason": "no setups above threshold",
                "scanned": result["scanned"]}

    today = datetime.now(timezone.utc)
    header = (f"📈 TECHNICAL SCREENER — {len(candidates)} STOCKS COILING — {today:%b %d, %Y}\n"
              "Self-found from price/volume technicals; 🔥 = also unusual call flow.\n"
              "(Educational, not financial advice.)")
    body = "\n\n".join(_format(s) for s in candidates)
    message = f"{header}\n\n{body}"

    channels = [TelegramAlerter(), DiscordAlerter()]
    for ch in channels:
        try:
            await ch.send_text(message)
        except Exception as exc:  # noqa: BLE001
            log.error("screener scan send failed", channel=ch.name, error=str(exc))
    tickers = [s["ticker"] for s in candidates]
    log.info("screener scan sent", tickers=tickers)
    return {"sent": True, "tickers": tickers, "scanned": result["scanned"]}
