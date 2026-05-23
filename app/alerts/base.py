"""Alert dispatch — fans a setup out to all configured channels."""
from __future__ import annotations

import asyncio

from app.alerts.discord import DiscordAlerter
from app.alerts.summary import generate_summary
from app.alerts.telegram import TelegramAlerter
from app.analysis.levels import levels_block
from app.config import settings
from app.core.logging import get_logger
from app.schemas.flow import ContractType, FlowEvent, FlowFeatureVector, ScoreResult

log = get_logger("alerts")


def _is_otm_call(event: FlowEvent) -> bool:
    """True if a call's strike is above spot (out-of-the-money). Unknown spot
    is treated as OTM so we don't silently drop alerts."""
    if event.spot is None:
        return True
    return event.strike > event.spot


class AlertDispatcher:
    def __init__(self):
        self.channels = [DiscordAlerter(), TelegramAlerter()]
        self._allowed = {
            c.strip() for c in settings.alert_classifications.split(",") if c.strip()
        }

    def should_alert(self, result: ScoreResult, event: FlowEvent | None = None,
                     features: FlowFeatureVector | None = None) -> bool:
        # Two independent paths: the conviction-confidence gate (needs rich data
        # incl. aggressor side), OR the raw unusual-activity gate (works on an
        # options-only plan). Either one firing is enough.
        if self._confidence_gate(result, event, features):
            return True
        if features is not None and self._unusual_activity_gate(event, features):
            return True
        return False

    def is_unusual_activity_only(self, result: ScoreResult, event: FlowEvent | None,
                                 features: FlowFeatureVector | None) -> bool:
        """True when the alert qualifies only via the unusual-activity gate (the
        conviction gate did not pass) — used to label the message accordingly."""
        return (not self._confidence_gate(result, event, features)
                and features is not None
                and self._unusual_activity_gate(event, features))

    def _unusual_activity_gate(self, event: FlowEvent | None,
                               features: FlowFeatureVector) -> bool:
        if not settings.alert_on_unusual_activity:
            return False
        return (features.is_opening
                and features.vol_oi >= settings.alert_unusual_vol_oi
                and features.premium >= settings.alert_unusual_premium
                and features.dte <= settings.alert_unusual_dte_max)

    def _confidence_gate(self, result: ScoreResult, event: FlowEvent | None = None,
                         features: FlowFeatureVector | None = None) -> bool:
        if result.confidence < settings.alert_min_confidence:
            return False
        if self._allowed and result.classification.value not in self._allowed:
            return False
        # Only alert on out-of-the-money calls; puts are exempt from this rule.
        if (settings.alert_calls_otm_only and event is not None
                and event.contract_type == ContractType.CALL
                and not _is_otm_call(event)):
            return False
        # Screener gates (feature-derived). Skipped when features aren't passed.
        if features is not None:
            if settings.alert_require_opening and not features.is_opening:
                return False
            # iv_rank == 0.5 is the "unknown" sentinel — exempt it so a missing
            # IV never silently suppresses an alert.
            if (features.iv_rank != 0.5
                    and features.iv_rank > settings.alert_max_iv_rank):
                return False
        # Multi-leg gate: only alert bullish structures when configured.
        if (settings.alert_bullish_structures_only and event is not None
                and event.is_spread and not event.is_bullish_structure):
            return False
        return True

    async def dispatch(self, event: FlowEvent, result: ScoreResult,
                       features: FlowFeatureVector | None = None) -> dict[str, str]:
        summary = generate_summary(event, result)
        if self.is_unusual_activity_only(result, event, features):
            summary = (f"UNUSUAL OPTIONS ACTIVITY — {event.ticker}\n"
                       "(flagged on volume/premium; conviction limited — no "
                       "buy/sell direction on this data plan)\n" + summary)
        try:
            block = await levels_block(event)
        except Exception as exc:  # noqa: BLE001 — levels are best-effort
            log.warning("levels lookup failed", ticker=event.ticker, error=str(exc))
            block = ""
        if block:
            summary = f"{summary}\n\n{block}"
        results = await asyncio.gather(
            *(ch.send(summary, event, result) for ch in self.channels),
            return_exceptions=True,
        )
        status = {}
        for ch, res in zip(self.channels, results):
            status[ch.name] = "error" if isinstance(res, Exception) else str(res)
            if isinstance(res, Exception):
                log.error("alert channel failed", channel=ch.name, error=str(res))
        return status
