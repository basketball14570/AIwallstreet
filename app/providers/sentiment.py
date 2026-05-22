"""Social + news sentiment aggregation (Twitter/X, Reddit, news).

Returns normalised 0-1 scores. Mock implementation by default; wire real
clients (tweepy / PRAW / NewsAPI) behind the same interface.
"""
from __future__ import annotations

import random

from app.config import settings
from app.core.logging import get_logger

log = get_logger("provider.sentiment")


class SentimentProvider:
    async def score(self, ticker: str) -> dict[str, float]:
        if not (settings.twitter_bearer_token or settings.news_api_key):
            return {"social": round(random.uniform(0, 1), 2),
                    "news": round(random.uniform(0, 1), 2)}
        # TODO: fan out to twitter/reddit/news clients, blend volume + polarity.
        return {"social": 0.0, "news": 0.0}
