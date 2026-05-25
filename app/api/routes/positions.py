"""Option positions the user holds — CRUD, current-price overview, and AI take."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.llm_analyst import ai_position_take
from app.config import settings
from app.core.logging import get_logger
from app.db.base import get_session
from app.db.models import Position
from app.providers.prices import PriceHistoryProvider

router = APIRouter(prefix="/positions", tags=["positions"])
log = get_logger("api.positions")
_prices = PriceHistoryProvider()


class PositionIn(BaseModel):
    ticker: str
    contract_type: str = "call"
    strike: float
    expiry: str           # ISO date, e.g. 2026-06-19
    contracts: int = 1
    entry_premium: float | None = None
    note: str | None = None


def _row_dict(p: Position) -> dict:
    return {"id": p.id, "ticker": p.ticker, "contract_type": p.contract_type,
            "strike": p.strike,
            "expiry": p.expiry.date().isoformat() if p.expiry else None,
            "contracts": p.contracts, "entry_premium": p.entry_premium,
            "note": p.note}


@router.get("")
async def list_positions(session: AsyncSession = Depends(get_session)):
    """Positions with live underlying price, moneyness, days left and % to strike."""
    rows = (await session.execute(
        select(Position).order_by(desc(Position.created_at))
    )).scalars().all()
    out = []
    for p in rows:
        d = _row_dict(p)
        spot = await _prices.current_price(p.ticker)
        d["spot"] = spot
        if spot:
            is_call = p.contract_type == "call"
            itm = (spot > p.strike) if is_call else (spot < p.strike)
            d["moneyness"] = "ITM" if itm else "OTM"
            d["pct_to_strike"] = round((p.strike - spot) / spot * 100, 1)
        exp = p.expiry.replace(tzinfo=timezone.utc) if p.expiry and p.expiry.tzinfo is None else p.expiry
        d["days_to_expiry"] = (exp.date() - datetime.now(timezone.utc).date()).days if exp else None
        out.append(d)
    return out


@router.post("", status_code=201)
async def add_position(body: PositionIn, session: AsyncSession = Depends(get_session)):
    try:
        expiry = datetime.fromisoformat(body.expiry)
    except ValueError as exc:
        raise HTTPException(400, "expiry must be ISO date, e.g. 2026-06-19") from exc
    p = Position(ticker=body.ticker.upper(), contract_type=body.contract_type.lower(),
                 strike=body.strike, expiry=expiry, contracts=body.contracts,
                 entry_premium=body.entry_premium, note=body.note)
    session.add(p)
    await session.commit()
    await session.refresh(p)
    return _row_dict(p)


@router.delete("/{position_id}", status_code=204)
async def remove_position(position_id: int, session: AsyncSession = Depends(get_session)):
    await session.execute(delete(Position).where(Position.id == position_id))
    await session.commit()


@router.get("/{position_id}/ai")
async def position_ai(position_id: int, session: AsyncSession = Depends(get_session)):
    """Position-aware AI take. Requires ANTHROPIC_API_KEY."""
    if not settings.anthropic_api_key:
        raise HTTPException(503, "AI analyst is disabled — set ANTHROPIC_API_KEY.")
    p = await session.get(Position, position_id)
    if not p:
        raise HTTPException(404, "position not found")
    pos = {"ticker": p.ticker, "contract_type": p.contract_type, "strike": p.strike,
           "expiry": p.expiry, "contracts": p.contracts,
           "entry_premium": p.entry_premium, "note": p.note}
    try:
        return await ai_position_take(pos)
    except Exception as exc:  # noqa: BLE001
        log.error("position ai failed", position=position_id, error=str(exc))
        raise HTTPException(502, f"AI analyst error: {exc}") from exc
