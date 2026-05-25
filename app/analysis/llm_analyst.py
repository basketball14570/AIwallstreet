"""AI analyst — Claude reasons over a ticker's flow + technicals + your levels.

Gathers everything the app knows about a ticker (mechanical technicals, trusted
analyst levels, recent unusual options flow) and asks Claude to write a real
analyst's take: what it sees, the option it would watch, and what would
invalidate the idea. Reasoning only — the user does the trading.

Disabled unless ANTHROPIC_API_KEY is set. The SDK is imported lazily so the app
boots fine without the package installed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import desc, select

from app.analysis.technicals import analyze
from app.config import settings
from app.core.logging import get_logger
from app.db.base import SessionLocal
from app.db.models import AnalystLevels, FlowFeatures, FlowScore, RawFlow

log = get_logger("analysis.llm")

# Frozen system prompt — the analyst persona and the hard rules. Kept first and
# stable so it caches across tickers (prompt-caching prefix match).
SYSTEM_PROMPT = """You are a careful options-flow analyst writing for a beginner \
retail trader. You are given structured data about ONE ticker: mechanical \
technical analysis, a trusted human analyst's weekly support/resistance levels, \
and recent unusual options-flow events. Write a concise, plain-English analyst \
take.

HARD RULES — follow all of them:
- This is EDUCATIONAL analysis, not financial advice. Say so.
- The options-flow data CANNOT confirm whether a trade was a BUY or a SELL. \
Never claim someone is "buying" or "betting" with certainty. Treat call/put \
flow as a directional HINT to investigate, not a confirmed signal.
- Be honest about uncertainty and conflicting signals. If the flow and the \
chart disagree, say so plainly. Do not manufacture false confidence.
- Options are high-risk and time-limited; remind the reader they can lose all \
their value.
- Do NOT invent data you weren't given (no made-up prices, news, or earnings).
- Keep it tight — a beginner should be able to read it in under a minute.

OUTPUT — use exactly these short sections with these headers:
THE SETUP: 2-3 sentences on what's notable (flow + where price sits vs levels).
THE LEAN: bullish / bearish / mixed, and why — with the buy/sell caveat.
THE CONTRACT TO WATCH: a realistic strike/expiry idea consistent with the lean \
and the typical daily move, framed as "one to watch," not a recommendation. If \
the data doesn't support a specific contract, say so.
LEVELS: the key level above and below to watch (prefer the analyst's levels).
WHAT WOULD INVALIDATE THIS: the price action that would prove the idea wrong.
RISK: one blunt sentence on the risk.
"""


POSITION_SYSTEM_PROMPT = """You are a careful options analyst writing for a \
beginner who ALREADY HOLDS the option contract described. Give an honest, \
plain-English read on their position using the structured data provided \
(position math, mechanical technicals, trusted analyst levels, recent flow).

HARD RULES — follow all of them:
- This is EDUCATIONAL analysis, not financial advice, and NOT a hold/sell \
instruction. Never tell them to hold, sell, or add.
- The options-flow data CANNOT confirm buys vs sells — treat flow as a hint.
- Be honest and direct, including bad news. If the position needs a big move in \
little time, say so plainly. If it's likely to expire worthless without a sharp \
move, say that.
- Use the position math you're given (moneyness, % move to strike/breakeven, \
days left). Do not invent prices, news, or earnings.
- Options can lose 100% of their value by expiry — make the time risk explicit.

OUTPUT — use exactly these short sections with these headers:
WHERE IT STANDS: in/out of the money, distance to strike, days left, breakeven.
WHAT NEEDS TO HAPPEN: the move (size and speed) required to profit by expiry, \
related to the stock's typical daily move.
CHART & FLOW: do the technicals, levels, and recent flow support or threaten \
the position? Be specific and honest about conflicts.
THE RISKS: blunt — time decay, the move needed, total-loss risk.
WATCH THESE LEVELS: the price levels that would confirm or kill the thesis.
"""


def _fmt(v) -> str:
    return "n/a" if v is None else (f"{v:g}" if isinstance(v, (int, float)) else str(v))


def _position_math(pos: dict, spot: float | None) -> dict:
    """Moneyness, distance to strike, days left, breakeven — all from real inputs."""
    is_call = pos["contract_type"] == "call"
    strike = pos["strike"]
    expiry = pos["expiry"]
    if isinstance(expiry, str):
        expiry_dt = datetime.fromisoformat(expiry)
    else:
        expiry_dt = expiry
    if expiry_dt.tzinfo is None:
        expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
    days_left = (expiry_dt - datetime.now(timezone.utc)).days
    out: dict = {"days_to_expiry": days_left}
    if spot:
        itm = (spot > strike) if is_call else (spot < strike)
        out["moneyness"] = "in-the-money" if itm else "out-of-the-money"
        out["pct_to_strike"] = round((strike - spot) / spot * 100, 1)
        prem = pos.get("entry_premium")
        if prem is not None:
            breakeven = strike + prem if is_call else strike - prem
            out["breakeven"] = round(breakeven, 2)
            out["pct_to_breakeven"] = round((breakeven - spot) / spot * 100, 1)
    return out


async def _gather_context(ticker: str) -> dict:
    ticker = ticker.upper()
    ta = await analyze(ticker)
    since = datetime.now(timezone.utc) - timedelta(days=7)
    async with SessionLocal() as session:
        analyst = await session.get(AnalystLevels, ticker)
        rows = (await session.execute(
            select(RawFlow, FlowScore, FlowFeatures)
            .join(FlowScore, FlowScore.flow_id == RawFlow.id)
            .join(FlowFeatures, FlowFeatures.flow_id == RawFlow.id)
            .where(RawFlow.ticker == ticker)
            .where(RawFlow.observed_at >= since)
            .order_by(desc(RawFlow.premium))
            .limit(12)
        )).all()
    flow = [{
        "type": f.contract_type, "strike": f.strike,
        "expiry": f"{f.expiry:%Y-%m-%d}", "premium": round(f.premium),
        "size": f.size, "side": f.side, "vol_oi": round(ff.vol_oi, 1),
        "is_opening": ff.is_opening, "classification": s.classification,
    } for f, s, ff in rows]
    return {"ticker": ticker, "technicals": ta, "analyst_levels": (
        {"resistances": analyst.resistances, "supports": analyst.supports,
         "clb36": analyst.clb36, "weekly_cpl": analyst.weekly_cpl}
        if analyst else None), "recent_flow": flow}


def _render_user_prompt(ctx: dict) -> str:
    ta = ctx["technicals"]
    lines = [f"TICKER: {ctx['ticker']}"]
    if ta.get("data_source") == "unavailable":
        lines.append("TECHNICALS: price history unavailable for this ticker.")
    else:
        lines += [
            f"PRICE: {_fmt(ta.get('current') or ta.get('spot'))} "
            f"(prev close {_fmt(ta.get('spot'))})",
            f"TREND: {ta.get('trend')}",
            f"RSI: {_fmt(ta.get('rsi'))} ({ta.get('rsi_state')}); "
            f"MACD: {ta.get('macd_state')}",
            f"MOVING AVGS: 20d {_fmt(ta.get('sma20'))}, 50d {_fmt(ta.get('sma50'))}, "
            f"200d {_fmt(ta.get('sma200'))}",
            f"TYPICAL DAILY MOVE (ATR): {_fmt(ta.get('atr'))} "
            f"({_fmt(ta.get('typical_move_pct'))}%)",
            f"PERF: 1d {_fmt(ta.get('chg_1d'))}%, 1w {_fmt(ta.get('chg_1w'))}%, "
            f"1m {_fmt(ta.get('chg_1m'))}%",
            f"OUR LEVELS: resistance {ta.get('resistances')}, "
            f"support {ta.get('supports')}",
            f"52W: {_fmt(ta.get('low_52w'))}–{_fmt(ta.get('high_52w'))} "
            f"({_fmt(ta.get('pct_from_high'))}% from high)",
        ]
    a = ctx["analyst_levels"]
    if a:
        lines.append(f"ANALYST WEEKLY LEVELS: resistance (above) {a['resistances']}, "
                     f"support (below) {a['supports']}")
    if ctx["recent_flow"]:
        lines.append("RECENT UNUSUAL FLOW (last 7d, biggest first; side may be "
                     "unknown):")
        for f in ctx["recent_flow"]:
            lines.append(
                f"  - {f['type']} ${_fmt(f['strike'])} exp {f['expiry']}: "
                f"${f['premium']:,} premium, {f['size']} ct, {f['vol_oi']}x OI, "
                f"side={f['side'] or 'unknown'}, "
                f"{'opening' if f['is_opening'] else 'not opening'}")
    else:
        lines.append("RECENT UNUSUAL FLOW: none recorded in the last 7 days.")
    return "\n".join(lines)


async def ai_analyst_take(ticker: str) -> dict:
    """Return Claude's analyst take. Raises RuntimeError if no API key."""
    if not settings.anthropic_api_key:
        raise RuntimeError("AI analyst is disabled (no ANTHROPIC_API_KEY set).")
    import anthropic  # lazy — app boots without the package

    ctx = await _gather_context(ticker)
    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=settings.llm_model,
        max_tokens=4000,
        thinking={"type": "adaptive"},
        output_config={"effort": settings.llm_effort},
        system=[{"type": "text", "text": SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": _render_user_prompt(ctx)}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    log.info("ai take", ticker=ticker.upper(), model=settings.llm_model,
             cache_read=resp.usage.cache_read_input_tokens,
             out_tokens=resp.usage.output_tokens)
    return {"ticker": ticker.upper(), "take": text, "model": settings.llm_model}


async def ai_position_take(pos: dict) -> dict:
    """Position-aware AI take for an option the user holds. `pos` keys: ticker,
    contract_type, strike, expiry, contracts, entry_premium, note."""
    if not settings.anthropic_api_key:
        raise RuntimeError("AI analyst is disabled (no ANTHROPIC_API_KEY set).")
    import anthropic  # lazy

    ticker = pos["ticker"].upper()
    ctx = await _gather_context(ticker)
    spot = (ctx["technicals"].get("current")
            or ctx["technicals"].get("spot"))
    math = _position_math(pos, spot)

    prem = pos.get("entry_premium")
    held = (f"YOUR POSITION: {pos.get('contracts', 1)}x {ticker} ${_fmt(pos['strike'])} "
            f"{pos['contract_type']} expiring "
            f"{pos['expiry'] if isinstance(pos['expiry'], str) else pos['expiry']:%Y-%m-%d}"
            + (f", entry premium ${_fmt(prem)}/contract" if prem is not None else "")
            + (f". Note: {pos['note']}" if pos.get("note") else ""))
    math_line = ("POSITION MATH: "
                 + ", ".join(f"{k}={v}" for k, v in math.items()))
    user_prompt = f"{held}\n{math_line}\n\n{_render_user_prompt(ctx)}"

    client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
    resp = await client.messages.create(
        model=settings.llm_model,
        max_tokens=4000,
        thinking={"type": "adaptive"},
        output_config={"effort": settings.llm_effort},
        system=[{"type": "text", "text": POSITION_SYSTEM_PROMPT,
                 "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": user_prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text").strip()
    log.info("ai position take", ticker=ticker, model=settings.llm_model,
             out_tokens=resp.usage.output_tokens)
    return {"ticker": ticker, "take": text, "model": settings.llm_model,
            "position_math": math}
