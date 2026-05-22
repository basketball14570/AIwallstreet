"""Domain models shared across providers, scoring and the API."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class Side(str, Enum):
    ASK = "ask"
    BID = "bid"
    MID = "mid"


class ContractType(str, Enum):
    CALL = "call"
    PUT = "put"


class Classification(str, Enum):
    NORMAL = "Normal Flow"
    HEDGING = "Hedging Flow"
    INSTITUTIONAL = "Institutional Positioning"
    WATCHLIST = "Watchlist Candidate"
    MOMENTUM = "Strong Momentum Setup"
    EXPLOSION = "Potential Explosion Setup"
    FAKE = "Fake / Low-Quality Flow"


class FlowEvent(BaseModel):
    """Normalised options-flow event emitted by every provider."""

    source: str
    external_id: str | None = None
    ticker: str
    contract_type: ContractType
    strike: float
    expiry: datetime
    side: Side | None = None
    is_sweep: bool = False
    is_spread: bool = False
    premium: float
    size: int
    spot: float | None = None
    observed_at: datetime
    raw: dict = Field(default_factory=dict)


class MarketContext(BaseModel):
    """Per-ticker reference data merged into the feature vector."""

    ticker: str
    float_shares: float | None = None
    short_interest_pct: float | None = None
    borrow_rate: float | None = None
    rel_options_volume: float | None = None
    stock_rvol: float | None = None
    oi_change_ratio: float | None = None
    dealer_gamma: float | None = None
    social_score: float = 0.0
    news_score: float = 0.0
    historical_similarity: float = 0.0


class FlowFeatureVector(BaseModel):
    ask_side_ratio: float = 0.0
    sweep_urgency: float = 0.0
    repeated_sweeps: int = 0
    at_midpoint: bool = False
    otm_pct: float = 0.0
    dte: float = 0.0
    rel_options_volume: float = 0.0
    stock_rvol: float = 0.0
    oi_change_ratio: float = 0.0
    float_shares: float | None = None
    short_interest_pct: float | None = None
    borrow_rate: float | None = None
    dealer_gamma: float | None = None
    social_score: float = 0.0
    news_score: float = 0.0
    historical_similarity: float = 0.0


class ScoreResult(BaseModel):
    classification: Classification
    confidence: float  # 0-100
    explosion_prob: float
    squeeze_prob: float
    momentum_prob: float
    fake_flow_prob: float
    component_scores: dict[str, float]
    reasons: list[str]
    model_version: str = "rules-0.1"
