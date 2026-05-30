"""Database schema.

Core tables:
  raw_flow         -- normalised options-flow events as ingested
  flow_features    -- engineered feature vector per event (one-to-one)
  flow_score       -- scoring-engine + ML output per event
  alert            -- dispatched alerts (audit trail + dedup)
  watchlist        -- user-curated tickers
  outcome          -- realised forward returns used for labelling / backtest
  journal_entry    -- user-saved trade ideas + the forward prices we track for them
  analyst_levels   -- trusted weekly support/resistance levels, imported by paste
  position         -- option contracts the user actually holds, for AI analysis
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base

# Cross-dialect JSON column: Postgres uses native JSONB (indexable, efficient);
# SQLite (the zero-infra "lite" / free-hosting path) falls back to generic JSON.
# Same Python dict/list interface either way, so models and queries are unchanged.
JSONType = JSONB().with_variant(JSON(), "sqlite")

# Auto-incrementing primary key. Postgres uses BIGINT; SQLite only auto-generates
# rowids for INTEGER PRIMARY KEY (BIGINT stays NULL), so it falls back to Integer.
BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class RawFlow(Base):
    __tablename__ = "raw_flow"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(32), index=True)  # unusual_whales etc.
    external_id: Mapped[str | None] = mapped_column(String(128), index=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)

    contract_type: Mapped[str] = mapped_column(String(4))  # call / put
    strike: Mapped[float] = mapped_column(Float)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    side: Mapped[str | None] = mapped_column(String(8))  # ask / bid / mid
    is_sweep: Mapped[bool] = mapped_column(Boolean, default=False)
    is_spread: Mapped[bool] = mapped_column(Boolean, default=False)
    premium: Mapped[float] = mapped_column(Float)  # total $ premium
    size: Mapped[int] = mapped_column(Integer)  # contracts
    spot: Mapped[float | None] = mapped_column(Float)  # underlying price at trade

    raw: Mapped[dict] = mapped_column(JSONType, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    features: Mapped["FlowFeatures"] = relationship(back_populates="flow", uselist=False)
    score: Mapped["FlowScore"] = relationship(back_populates="flow", uselist=False)

    __table_args__ = (
        Index("ix_raw_flow_ticker_time", "ticker", "observed_at"),
    )


class FlowFeatures(Base):
    __tablename__ = "flow_features"

    flow_id: Mapped[int] = mapped_column(
        ForeignKey("raw_flow.id", ondelete="CASCADE"), primary_key=True
    )
    # Execution micro-structure
    ask_side_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    sweep_urgency: Mapped[float] = mapped_column(Float, default=0.0)
    repeated_sweeps: Mapped[int] = mapped_column(Integer, default=0)
    at_midpoint: Mapped[bool] = mapped_column(Boolean, default=False)
    # Contract geometry
    otm_pct: Mapped[float] = mapped_column(Float, default=0.0)  # +ve = OTM
    dte: Mapped[float] = mapped_column(Float, default=0.0)
    # Volume / liquidity
    rel_options_volume: Mapped[float] = mapped_column(Float, default=0.0)
    stock_rvol: Mapped[float] = mapped_column(Float, default=0.0)
    oi_change_ratio: Mapped[float] = mapped_column(Float, default=0.0)
    # Squeeze fuel
    float_shares: Mapped[float | None] = mapped_column(Float)
    short_interest_pct: Mapped[float | None] = mapped_column(Float)
    borrow_rate: Mapped[float | None] = mapped_column(Float)
    dealer_gamma: Mapped[float | None] = mapped_column(Float)
    # Per-contract / underlying screener context
    iv_rank: Mapped[float] = mapped_column(Float, default=0.5)
    vol_oi: Mapped[float] = mapped_column(Float, default=0.0)
    is_opening: Mapped[bool] = mapped_column(Boolean, default=False)
    days_to_earnings: Mapped[float] = mapped_column(Float, default=999.0)
    bullish_structure: Mapped[float] = mapped_column(Float, default=0.0)
    follow_through: Mapped[float] = mapped_column(Float, default=0.0)
    ticker_hit_rate: Mapped[float] = mapped_column(Float, default=0.0)
    # Catalyst
    social_score: Mapped[float] = mapped_column(Float, default=0.0)
    news_score: Mapped[float] = mapped_column(Float, default=0.0)
    historical_similarity: Mapped[float] = mapped_column(Float, default=0.0)

    vector: Mapped[dict] = mapped_column(JSONType, default=dict)  # full raw feature dict

    flow: Mapped["RawFlow"] = relationship(back_populates="features")


class FlowScore(Base):
    __tablename__ = "flow_score"

    flow_id: Mapped[int] = mapped_column(
        ForeignKey("raw_flow.id", ondelete="CASCADE"), primary_key=True
    )
    classification: Mapped[str] = mapped_column(String(40), index=True)
    confidence: Mapped[float] = mapped_column(Float)  # 0-100
    explosion_prob: Mapped[float] = mapped_column(Float)
    squeeze_prob: Mapped[float] = mapped_column(Float)
    momentum_prob: Mapped[float] = mapped_column(Float)
    fake_flow_prob: Mapped[float] = mapped_column(Float)
    component_scores: Mapped[dict] = mapped_column(JSONType, default=dict)
    reasons: Mapped[dict] = mapped_column(JSONType, default=dict)
    regime: Mapped[str] = mapped_column(String(40), default="neutral", index=True)
    model_version: Mapped[str] = mapped_column(String(40), default="rules-0.1")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    flow: Mapped["RawFlow"] = relationship(back_populates="score")


class Alert(Base):
    __tablename__ = "alert"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    flow_id: Mapped[int] = mapped_column(ForeignKey("raw_flow.id", ondelete="CASCADE"))
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    classification: Mapped[str] = mapped_column(String(40))
    confidence: Mapped[float] = mapped_column(Float)
    summary: Mapped[str] = mapped_column(Text)
    channels: Mapped[dict] = mapped_column(JSONType, default=dict)  # delivery status
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class Watchlist(Base):
    __tablename__ = "watchlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Outcome(Base):
    """Realised forward returns for labelling and backtest evaluation."""

    __tablename__ = "outcome"

    flow_id: Mapped[int] = mapped_column(
        ForeignKey("raw_flow.id", ondelete="CASCADE"), primary_key=True
    )
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    ret_1d: Mapped[float | None] = mapped_column(Float)
    ret_3d: Mapped[float | None] = mapped_column(Float)
    ret_5d: Mapped[float | None] = mapped_column(Float)
    max_runup: Mapped[float | None] = mapped_column(Float)
    max_drawdown: Mapped[float | None] = mapped_column(Float)
    label: Mapped[int | None] = mapped_column(Integer)  # 1 = explosive move
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class JournalEntry(Base):
    """A trade idea the user saved from an alert. We snapshot the entry price and
    then fill forward prices over the next few days to score how it played out."""

    __tablename__ = "journal_entry"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    contract_type: Mapped[str] = mapped_column(String(4))  # call / put
    strike: Mapped[float | None] = mapped_column(Float)
    expiry: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lean: Mapped[str] = mapped_column(String(8))  # bullish / bearish
    entry_price: Mapped[float | None] = mapped_column(Float)  # underlying spot at save

    # Forward prices we backfill (all underlying prices, not the option premium).
    intraday_price: Mapped[float | None] = mapped_column(Float)   # +N hours, same day
    next_open_price: Mapped[float | None] = mapped_column(Float)  # next session open
    next_close_price: Mapped[float | None] = mapped_column(Float)  # next session close
    day3_price: Mapped[float | None] = mapped_column(Float)        # ~3 sessions later

    note: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(16), default="alert")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class AnalystLevels(Base):
    """Weekly support/resistance levels from a trusted analyst, imported by
    pasting the analyst's table. One row per ticker (re-import overwrites)."""

    __tablename__ = "analyst_levels"

    ticker: Mapped[str] = mapped_column(String(16), primary_key=True)
    clb36: Mapped[float | None] = mapped_column(Float)        # CLB36+B1:C column
    weekly_cpl: Mapped[float | None] = mapped_column(Float)   # WEEKLY CPL column
    resistances: Mapped[list] = mapped_column(JSONType, default=list)  # above price
    supports: Mapped[list] = mapped_column(JSONType, default=list)     # below price
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Position(Base):
    """An option contract the user actually holds — for position-aware AI takes."""

    __tablename__ = "position"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(16), index=True)
    contract_type: Mapped[str] = mapped_column(String(4))  # call / put
    strike: Mapped[float] = mapped_column(Float)
    expiry: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    contracts: Mapped[int] = mapped_column(Integer, default=1)
    entry_premium: Mapped[float | None] = mapped_column(Float)  # $ paid per contract
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
