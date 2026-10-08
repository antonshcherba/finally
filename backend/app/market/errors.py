"""Exceptions raised by the market data layer.

The API layer maps these to the error codes in PLAN.md §8.
"""


class MarketDataError(Exception):
    """Base class for market data errors."""


class InvalidTicker(MarketDataError, ValueError):  # noqa: N818
    """Not 1-5 letters after trimming/uppercasing -> 400 invalid_ticker."""


class UnknownTicker(MarketDataError, ValueError):  # noqa: N818
    """Well-formed, but the active source cannot price it -> 400 unknown_ticker."""


class PriceUnavailable(MarketDataError):  # noqa: N818
    """No price in the cache yet (or a transient lookup failure) -> 503 price_unavailable."""


class MarketDataAuthError(MarketDataError):
    """Massive rejected the key, or the plan lacks the snapshot endpoint."""
