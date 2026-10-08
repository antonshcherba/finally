"""Abstract interface for market data sources."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterable

from .errors import InvalidTicker, UnknownTicker

logger = logging.getLogger(__name__)


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push price updates into a shared PriceCache on their own
    schedule. Downstream code never calls the data source directly for prices —
    it reads from the cache.

    Tickers passed to any method must already be valid symbols (1-5 letters);
    implementations trim and uppercase them defensively.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL", ...])
        # ... app runs ...
        await source.set_tickers(watchlist | held_positions)
        # ... app shutting down ...
        await source.stop()
    """

    mode: str  # "simulator" | "massive"

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing price updates for the given tickers.

        Starts a background task that periodically writes to the PriceCache.
        Must be called exactly once. Calling start() twice is undefined behavior.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Stop the background task and release resources.

        Safe to call multiple times. After stop(), the source will not write
        to the cache again.
        """

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Add a ticker to the active set. No-op if already present.

        Simulator: priced immediately. Massive: priced as soon as an out-of-cycle
        poll completes. Raises UnknownTicker if the source can never price it.
        """

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Remove a ticker from the active set. No-op if not present.

        Also removes the ticker from the PriceCache.
        """

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Return the current list of actively tracked tickers."""

    @abstractmethod
    async def validate_ticker(self, ticker: str) -> bool:
        """True if this source can price `ticker` (already normalized).

        Raises PriceUnavailable on a transient lookup failure, so a network blip
        is never reported as an unknown ticker.
        """

    async def set_tickers(self, desired: Iterable[str]) -> None:
        """Make the tracked set equal `desired` (watchlist ∪ open positions).

        Invalid or unsupported tickers are skipped with a warning rather than
        failing the whole reconcile.
        """
        want = {t.strip().upper() for t in desired}
        have = set(self.get_tickers())
        for ticker in sorted(want - have):
            try:
                await self.add_ticker(ticker)
            except (InvalidTicker, UnknownTicker):
                logger.warning("Not tracking %r: not supported by the %s source", ticker, self.mode)
        for ticker in sorted(have - want):
            await self.remove_ticker(ticker)
