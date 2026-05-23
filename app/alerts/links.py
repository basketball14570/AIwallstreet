"""Builds the clickable 'Add to journal' link embedded in each alert."""
from __future__ import annotations

from urllib.parse import urlencode

from app.config import settings
from app.schemas.flow import ContractType, FlowEvent


def journal_link(event: FlowEvent) -> str:
    lean = "bullish" if event.contract_type == ContractType.CALL else "bearish"
    params = {
        "ticker": event.ticker,
        "type": event.contract_type.value,
        "strike": event.strike,
        "expiry": f"{event.expiry:%Y-%m-%d}",
        "lean": lean,
        "source": "alert",
    }
    if event.spot is not None:
        params["entry_price"] = event.spot
    return f"{settings.public_base_url.rstrip('/')}/journal/add?{urlencode(params)}"
