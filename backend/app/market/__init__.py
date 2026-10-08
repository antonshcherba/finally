"""Market data subsystem for FinAlly.

Public API:
    PriceUpdate         - Immutable price snapshot dataclass
    PriceCache          - Thread-safe in-memory price store
    MarketDataSource    - Abstract interface for data providers
    create_market_data_source - Factory that selects simulator or Massive (unstarted)
    start_market_data   - Create + start a source, falling back to the simulator
    create_stream_router - FastAPI router factory for SSE endpoint
    normalize_ticker, require_known, require_price - API-boundary helpers
    InvalidTicker, UnknownTicker, PriceUnavailable, MarketDataAuthError - errors
"""

from .cache import PriceCache
from .errors import (
    InvalidTicker,
    MarketDataAuthError,
    MarketDataError,
    PriceUnavailable,
    UnknownTicker,
)
from .factory import create_market_data_source, start_market_data
from .interface import MarketDataSource
from .models import PriceUpdate
from .stream import create_stream_router
from .tickers import normalize_ticker, require_known, require_price

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "MarketDataSource",
    "create_market_data_source",
    "start_market_data",
    "create_stream_router",
    "normalize_ticker",
    "require_known",
    "require_price",
    "MarketDataError",
    "InvalidTicker",
    "UnknownTicker",
    "PriceUnavailable",
    "MarketDataAuthError",
]
