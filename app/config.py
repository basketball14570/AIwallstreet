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
    env: str = "dev"
    log_level: str = "INFO"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
