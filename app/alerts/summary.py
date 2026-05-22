"""Build a human-readable trade summary explaining WHY a setup scored highly.

Template-based for the MVP. Swap `generate_summary` for an LLM call to produce
richer natural-language summaries without changing callers.
"""
from __future__ import annotations

from app.schemas.flow import FlowEvent, ScoreResult


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
        "Why:",
    ]
    lines += [f"  • {r}" for r in (p.reasons or ["No standout signals"])]
    return "\n".join(lines)
