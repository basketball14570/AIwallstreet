"""Domain models shared across providers, scoring and the API."""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field


class Side(str, Enum):
    ASK = "ask"
    BID = "bid"
    MID = "mid"


class ContractType(str, Enum):
    CALL = "call"
    PUT = "put"


class Structure(str, Enum):
    """Multi-leg shape, when a provider can infer it. ``SINGLE`` is a lone
    contract; the rest classify the directional intent of a spread so the
    engine can stop treating every multi-leg print as a hedge."""

    SINGLE = "single"
    CALL_VERTICAL = "call_vertical"   # bullish: long lower-strike call spread
    PUT_VERTICAL = "put_vertical"     # bearish / hedge
    RISK_REVERSAL = "risk_reversal"   # bullish: short put / long call
    COLLAR = "collar"                 # hedge: long put / short call vs stock
    CONDOR = "condor"                 # neutral: range-bound vol structure
    UNKNOWN = "unknown"               # multi-leg but shape not identified


# Structures that express a directional *bullish* bet (a position, not a hedge).
BULLISH_STRUCTURES = {Structure.CALL_VERTICAL, Structure.RISK_REVERSAL}
# Structures that are range/neutral or protective (treat as hedging-leaning).
NEUTRAL_STRUCTURES = {Structure.CONDOR, Structure.COLLAR, Structure.PUT_VERTICAL}


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
    structure: Structure = Structure.SINGLE
    premium: float
    size: int
    spot: float | None = None
    # Per-contract microstructure (populated by providers when available).
    iv: float | None = None             # contract implied volatility (decimal)
    open_interest: int | None = None    # open interest at observation
    vol_oi: float | None = None         # day volume / open interest
    observed_at: datetime
    raw: dict = Field(default_factory=dict)

    @property
    def is_bullish_structure(self) -> bool:
        return self.structure in BULLISH_STRUCTURES

    @property
    def is_neutral_structure(self) -> bool:
        # A spread we couldn't classify is treated as neutral/hedge-leaning.
        return self.structure in NEUTRAL_STRUCTURES or (
            self.is_spread and self.structure in (Structure.SINGLE, Structure.UNKNOWN)
        )


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
    iv_rank: float | None = None         # 0-1 rank of current IV vs its history
    days_to_earnings: float | None = None  # calendar days to next earnings
    ticker_hit_rate: float | None = None   # historical alert hit-rate prior
    social_score: float = 0.0
    news_score: float = 0.0
    historical_similarity: float = 0.0


class FlowFeatureVector(BaseModel):
    ask_side_ratio: float = 0.0
    sweep_urgency: float = 0.0
    repeated_sweeps: int = 0
    at_midpoint: bool = False
    is_put: bool = False
    is_spread: bool = False
    premium: float = 0.0
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
    # Per-contract / underlying context added for the screener.
    iv_rank: float = 0.5               # 0=cheap vol, 1=rich vol; 0.5 = unknown
    vol_oi: float = 0.0                # day volume / open interest (capped)
    is_opening: bool = False           # vol_oi >= 1 => new positioning
    days_to_earnings: float = 999.0    # calendar days to next earnings
    bullish_structure: float = 0.0     # 1.0 if a bullish multi-leg structure
    follow_through: float = 0.0        # 0-1 prior flow on this contract built up
    ticker_hit_rate: float = 0.0       # historical alert hit-rate for the name
    # Intra-event sequence features (per ticker, rolling window). Captured for
    # temporal modelling and fed lightly into conviction today.
    seq_cadence_accel: float = 0.0     # >0 => sweeps arriving faster
    seq_strike_ladder: float = 0.0     # 0-1 => strikes laddering up over time
    seq_premium_velocity: float = 0.0  # $ premium / second in window
    seq_count: int = 0                 # events in the window


class ScoreResult(BaseModel):
    model_config = ConfigDict(protected_namespaces=())

    classification: Classification
    confidence: float  # 0-100
    explosion_prob: float
    squeeze_prob: float
    momentum_prob: float
    fake_flow_prob: float
    component_scores: dict[str, float]
    reasons: list[str]
    regime: str = "neutral"
    model_version: str = "rules-0.1"
