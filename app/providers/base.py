"""Provider abstractions.

A FlowProvider streams normalised FlowEvents. A ContextProvider enriches a
ticker with reference data (float, short interest, gamma, sentiment ...).
Implementations degrade gracefully to mock data when no API key is set so the
MVP is runnable end-to-end offline.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from app.schemas.flow import FlowEvent, MarketContext


class FlowProvider(ABC):
    name: str

    @abstractmethod
    def stream(self) -> AsyncIterator[FlowEvent]:
        ...


class ContextProvider(ABC):
    @abstractmethod
    async def get_context(self, ticker: str) -> MarketContext:
        ...
