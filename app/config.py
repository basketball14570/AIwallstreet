"""Centralised application settings, loaded from environment / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Providers
    unusual_whales_api_key: str = ""
    polygon_api_key: str = ""
    twitter_bearer_token: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    news_api_key: str = ""
    # Optional free earnings-calendar source. When set, alert cards show a
    # "reports in N days" warning; left blank, no earnings line is shown (we
    # never fabricate one — Polygon's plan doesn't include earnings dates).
    finnhub_api_key: str = ""
    # AI analyst (Anthropic). When ANTHROPIC_API_KEY is set, the dashboard's
    # "AI analyst take" and the proactive scanner use Claude to reason over the
    # flow + technicals + your levels. Left blank, those features are disabled.
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-4-7"
    llm_effort: str = "high"            # low | medium | high | xhigh | max
    # Proactive nightly scan is OFF by default — the API key alone only powers
    # the on-demand "AI take" button. Flip this on when you want the daily push.
    ai_scan_enabled: bool = False
    ai_scan_top_n: int = 3              # how many top setups the daily scan writes up

    # Datastores
    database_url: str = "postgresql+asyncpg://flow:flow@localhost:5432/flow"
    redis_url: str = "redis://localhost:6379/0"

    # Alerts
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # Flow source: auto (polygon if key else synthetic) | synthetic | polygon | unusual_whales
    flow_provider: str = "auto"
    # Tickers to scan for unusual options flow (Polygon provider).
    watch_tickers: str = "TSLA,NVDA,AAPL,AMD,PLTR,SOFI,GME,AMC,MARA,RIVN"
    polygon_flow_poll_sec: float = 30.0
    # Max snapshot pages to pull per ticker per cycle (250 contracts/page). The
    # endpoint can't sort by volume server-side, so we page the chain and keep
    # the most active contracts client-side. Higher = fuller coverage, more API
    # calls. 0/1 = single page.
    polygon_flow_max_pages: int = 6
    # Cap on contracts kept (by day volume) per ticker after paging.
    polygon_flow_top_contracts: int = 250
    # Minimum day-volume/open-interest ratio for a contract to count as unusual.
    # Lowered while testing so more flow surfaces; raise to tighten.
    flow_min_vol_oi: float = 0.35
    # Minimum total premium ($) for a synthesised flow event.
    flow_min_premium: float = 10_000.0

    # Behaviour
    alert_min_confidence: float = 70.0
    # Which classifications may fire an alert. Comma-separated; blank = any.
    # Defaults to explosion + strong momentum setups only (skip routine/hedging).
    alert_classifications: str = "Potential Explosion Setup,Strong Momentum Setup"
    # Only alert on out-of-the-money calls; puts may alert at any moneyness.
    alert_calls_otm_only: bool = True
    # Screener gates (all default to "off" so behaviour is unchanged unless set):
    # Require the contract's day-volume to exceed open interest (a freshly
    # opened position, not a close-out) before alerting.
    alert_require_opening: bool = False
    # Reject alerts whose IV-rank exceeds this (i.e. don't chase richly-priced
    # vol). 1.0 disables the gate. IV-rank 0.5 means "unknown".
    alert_max_iv_rank: float = 1.0
    # For multi-leg flow, only alert when the structure is bullish (call
    # vertical / risk reversal); skip neutral/hedge structures.
    alert_bullish_structures_only: bool = False
    # "Unusual Options Activity" alert — fires on the raw screener signals
    # (vol/OI, premium, opening, near-dated) independent of the conviction
    # confidence score. This is the path that works on data plans WITHOUT
    # option quotes/trades, where bought-vs-sold conviction can't be computed.
    alert_on_unusual_activity: bool = True
    alert_unusual_vol_oi: float = 5.0          # day volume >= 5x open interest
    alert_unusual_premium: float = 500_000.0   # min total premium ($)
    alert_unusual_dte_max: float = 60.0        # near-dated only (calendar days)
    # Anti-spam. On startup the scanner sees the whole day's accumulated volume
    # at once; the grace window lets that backlog populate the dashboard WITHOUT
    # firing alerts, so only genuinely new activity after start alerts. The
    # cooldown stops one busy contract from alerting over and over.
    alert_startup_grace_sec: float = 120.0
    alert_cooldown_min: float = 360.0          # don't re-alert a contract for 6h
    # Watchlist price alerts: poll your watchlist this often and ping when a name
    # crosses its breakout (resistance) or breakdown (support) level.
    watchlist_poll_sec: float = 300.0
    # "Approaching a level" heads-up: ping when price comes within this % of a
    # watched level (chart or analyst), before it actually crosses.
    watchlist_approach_pct: float = 0.5
    # Trade-idea journal. public_base_url is the address your phone can reach the
    # app at — it's used to build the clickable "Add to journal" link in alerts
    # (set it to your LAN IP or a tunnel URL so the link works off-machine).
    public_base_url: str = "http://localhost:8000"
    journal_intraday_hours: float = 3.0   # snapshot price this many hours after save
    journal_poll_sec: float = 600.0       # how often the backfiller runs
    env: str = "dev"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
