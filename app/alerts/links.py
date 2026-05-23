"""Builds the clickable 'Add to journal' links embedded in alerts and the digest."""
from __future__ import annotations

from urllib.parse import urlencode

from app.config import settings
from app.schemas.flow import ContractType, FlowEvent


def journal_url(ticker: str, contract_type: str, strike: float | None,
                expiry_iso: str | None, entry_price: float | None = None,
                source: str = "alert") -> str:
    lean = "bullish" if contract_type == "call" else "bearish"
    params: dict = {"ticker": ticker, "type": contract_type, "lean": lean,
                    "source": source}
    if strike is not None:
        params["strike"] = strike
    if expiry_iso:
        params["expiry"] = expiry_iso
    if entry_price is not None:
        params["entry_price"] = entry_price
    return f"{settings.public_base_url.rstrip('/')}/journal/add?{urlencode(params)}"


def journal_link(event: FlowEvent) -> str:
    ctype = (event.contract_type.value
             if isinstance(event.contract_type, ContractType)
             else str(event.contract_type))
    return journal_url(event.ticker, ctype, event.strike,
                       f"{event.expiry:%Y-%m-%d}", entry_price=event.spot)
