"""Factory for creating market data sources."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .errors import MarketDataAuthError
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)

DEFAULT_MASSIVE_POLL_INTERVAL = 15.0


def _poll_interval_from_env() -> float:
    raw = os.environ.get("MASSIVE_POLL_INTERVAL", "").strip()
    if not raw:
        return DEFAULT_MASSIVE_POLL_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            "Invalid MASSIVE_POLL_INTERVAL=%r; using %.0fs", raw, DEFAULT_MASSIVE_POLL_INTERVAL
        )
        return DEFAULT_MASSIVE_POLL_INTERVAL
    return max(value, 1.0)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Create the appropriate market data source based on environment variables.

    - MASSIVE_API_KEY set and non-empty → MassiveDataSource (real market data)
    - Otherwise → SimulatorDataSource (GBM simulation)

    Returns an unstarted source. Caller must await source.start(tickers).
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()

    if api_key:
        interval = _poll_interval_from_env()
        logger.info("Market data source: Massive API (poll every %.1fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)

    logger.info("Market data source: GBM Simulator")
    return SimulatorDataSource(price_cache=price_cache)


async def start_market_data(price_cache: PriceCache, tickers: list[str]) -> MarketDataSource:
    """Create and start a source; what the FastAPI lifespan calls.

    A Massive key the API rejects (bad key, or a free plan without snapshots)
    falls back to the simulator so the app always starts. Check `source.mode`
    to see which one is live.
    """
    source = create_market_data_source(price_cache)
    try:
        await source.start(tickers)
    except MarketDataAuthError as e:
        logger.error("Massive rejected the API key or plan (%s); using the simulator", e)
        await source.stop()
        source = SimulatorDataSource(price_cache=price_cache)
        await source.start(tickers)
    return source
