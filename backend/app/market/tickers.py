"""Ticker normalization and validation helpers used at the API boundary."""

from __future__ import annotations

import re

from .cache import PriceCache
from .errors import InvalidTicker, PriceUnavailable, UnknownTicker
from .interface import MarketDataSource
from .models import PriceUpdate

_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")


def normalize_ticker(raw: str) -> str:
    """Trim and uppercase; raise InvalidTicker unless the result is 1-5 letters."""
    ticker = raw.strip().upper() if isinstance(raw, str) else ""
    if not _TICKER_RE.match(ticker):
        raise InvalidTicker(f"Invalid ticker: {raw!r}")
    return ticker


async def require_known(source: MarketDataSource, raw: str) -> str:
    """Normalize `raw` and confirm the active source can price it.

    Raises InvalidTicker, UnknownTicker, or PriceUnavailable (transient lookup failure).
    """
    ticker = normalize_ticker(raw)
    if not await source.validate_ticker(ticker):
        raise UnknownTicker(f"Unknown ticker: {ticker}")
    return ticker


def require_price(cache: PriceCache, ticker: str) -> PriceUpdate:
    """Latest cached update, or PriceUnavailable if the ticker has no price yet."""
    update = cache.get(ticker)
    if update is None:
        raise PriceUnavailable(f"No price available for {ticker}")
    return update
