"""Beginner-friendly 'trade idea card'.

Synthesizes one options-flow event + its score + the ticker's chart read into a
plain-English explanation: what happened, what it might mean, the directional
lean (with honest caveats), a where-it-could-go/where-the-thesis-breaks plan
from the chart levels, the risks, and a glossary footnote.

This is educational reasoning, NOT a buy command — and it says so. With an
options-only data plan we can't confirm whether the flow was a buy or a sell, so
the lean is always presented as a hint, never a fact.
"""
from __future__ import annotations

from app.analysis.glossary import explain_terms
from app.analysis.technicals import analyze
from app.core.logging import get_logger
from app.schemas.flow import (
    ContractType,
    FlowEvent,
    FlowFeatureVector,
    ScoreResult,
)

log = get_logger("analysis.trade_idea")


def _money(v: float | None) -> str:
    return "—" if v is None else f"${v:,.2f}"


def _earnings_line(features: FlowFeatureVector | None) -> str | None:
    """Warn when earnings land soon — but only with a REAL date (999.0 is the
    'unknown' sentinel, so we stay silent rather than fabricate one)."""
    if features is None:
        return None
    dte = features.days_to_earnings
    if dte is None or dte >= 999 or dte < 0 or dte > 14:
        return None
    when = "today" if dte < 1 else ("tomorrow" if dte < 2 else f"in {dte:.0f} days")
    return (f"\n⚠️ EARNINGS {when.upper()}:\n{when.capitalize()} this company "
            "reports earnings. Unusual flow right before earnings is often just "
            "event positioning — a binary bet on the report — and options get "
            "expensive and extra-risky into the print. Tread carefully.")


async def build_trade_idea(event: FlowEvent, result: ScoreResult,
                           features: FlowFeatureVector | None = None) -> str:
    """Render the full plain-English card as text (for Telegram/Discord/preview)."""
    is_call = event.contract_type == ContractType.CALL
    kind = "CALL" if is_call else "PUT"
    spot = event.spot
    lines: list[str] = []

    # 1) Headline
    lines.append(f"UNUSUAL {kind} ACTIVITY — {event.ticker}")

    # 2) What happened (plain English)
    vol_oi = f"{event.vol_oi:.1f}x its open interest " if event.vol_oi else ""
    lines.append(
        f"\nWHAT HAPPENED:\n"
        f"{event.size:,} {kind.lower()} contracts at the ${event.strike:g} strike, "
        f"expiring {event.expiry:%b %d, %Y}, traded for about ${event.premium:,.0f}. "
        f"That volume is {vol_oi}— a sign someone opened a fresh, sizeable position."
        + (f" Stock is near {_money(spot)}." if spot else "")
    )

    # 3) Directional lean (with the honest caveat)
    if is_call:
        lean = ("Calls are an upside bet, so this leans BULLISH — someone may be "
                "positioning for the stock to rise.")
    else:
        lean = ("Puts are a downside bet, so this leans BEARISH — or it's someone "
                "buying protection (a hedge).")
    lines.append(
        f"\nWHAT IT MIGHT MEAN:\n{lean}\n"
        "Important: your data plan can't see whether this was a BUY or a SELL, so "
        "treat the direction as a hint to investigate — not a confirmed signal."
    )

    earn = _earnings_line(features)
    if earn:
        lines.append(earn)

    # 4) Chart read + a where-it-could-go / where-it-breaks plan
    try:
        ta = await analyze(event.ticker)
    except Exception as exc:  # noqa: BLE001
        log.warning("trade-idea TA failed", ticker=event.ticker, error=str(exc))
        ta = {"data_source": "unavailable"}

    if ta.get("data_source") != "unavailable":
        spot = spot or ta.get("spot")
        rsi = ta.get("rsi")
        rsi_note = f" RSI is {rsi} ({ta.get('rsi_state')})." if rsi is not None else ""
        lines.append(f"\nCHART READ:\n{ta.get('trend','')}.{rsi_note}")

        resist = ta.get("resistances") or []
        support = ta.get("supports") or []
        if is_call:
            target = (resist[0] if resist else ta.get("range_high"))
            broke = (support[0] if support else ta.get("range_low"))
            plan = (f"If it plays out, the next upside level to watch is "
                    f"{_money(target)}; the idea is wrong if it loses support "
                    f"around {_money(broke)}.")
        else:
            target = (support[0] if support else ta.get("range_low"))
            broke = (resist[0] if resist else ta.get("range_high"))
            plan = (f"If it plays out, the next downside level is {_money(target)}; "
                    f"the idea is wrong if it reclaims {_money(broke)}.")
        lines.append(f"\nLEVELS TO WATCH:\nNow {_money(spot)} → target {_money(target)}; "
                     f"thesis-break {_money(broke)}.\n{plan}")
    else:
        lines.append("\nCHART READ:\nPrice history wasn't available for this ticker.")

    # 5) Risk — never let a beginner forget this
    lines.append(
        f"\nRISK:\nOptions are high-risk and time-limited. This contract expires "
        f"{event.expiry:%b %d, %Y} — if {event.ticker} doesn't move enough by then, "
        f"it can lose most or ALL of its value. Never risk money you can't lose."
    )

    # 6) Glossary footnote for the jargon actually used
    body = "\n".join(lines)
    terms = explain_terms(body)
    if terms:
        lines.append("\nTERMS:\n" + "\n".join(f"• {t}" for t in terms))

    lines.append("\nEducational information, not financial advice.")
    return "\n".join(lines)
