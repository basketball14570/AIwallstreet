"""Build a human-readable trade summary explaining WHY a setup scored highly.

Template-based for the MVP. Swap `generate_summary` for an LLM call to produce
richer natural-language summaries without changing callers.
"""
from __future__ import annotations

from app.schemas.flow import FlowEvent, ScoreResult


def _signals_line(event: FlowEvent, result: ScoreResult) -> str | None:
    """Compact one-liner of the screener signals carried on the event/scores."""
    parts: list[str] = []
    if event.vol_oi is not None:
        tag = " (opening)" if event.vol_oi >= 1 else ""
        parts.append(f"vol/OI {event.vol_oi:.1f}x{tag}")
    if event.iv is not None:
        parts.append(f"IV {event.iv:.0%}")
    if event.is_bullish_structure:
        parts.append("bullish spread")
    ft = result.component_scores.get("follow_through", 0.0)
    if ft and ft > 0.5:
        parts.append(f"follow-through {ft:.0%}")
    hit = result.component_scores.get("historical", 0.0)
    if hit and hit >= 0.6:
        parts.append(f"hist hit-rate {hit:.0%}")
    return "signals: " + " | ".join(parts) if parts else None


def generate_summary(event: FlowEvent, result: ScoreResult) -> str:
    p = result
    lines = [
        f"{event.ticker} — {p.classification.value}  (confidence {p.confidence:.0f}/100)",
        (
            f"{event.size}x {event.contract_type.value} ${event.strike:g} "
            f"exp {event.expiry:%Y-%m-%d}  •  ${event.premium:,.0f} premium"
            + (f"  •  spot ${event.spot:g}" if event.spot else "")
        ),
        (
            f"explosion {p.explosion_prob:.0%} | squeeze {p.squeeze_prob:.0%} | "
            f"momentum {p.momentum_prob:.0%} | fake {p.fake_flow_prob:.0%}"
        ),
    ]
    sig = _signals_line(event, result)
    if sig:
        lines.append(sig)
    lines.append("Why:")
    lines += [f"  • {r}" for r in (p.reasons or ["No standout signals"])]
    return "\n".join(lines)
