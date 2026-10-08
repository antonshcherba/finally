"""Massive (formerly Polygon.io) REST poller for real market data."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from massive import RESTClient
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .errors import MarketDataAuthError, PriceUnavailable
from .interface import MarketDataSource
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 120.0

_AUTH_MARKERS = ("not_authorized", "unauthorized", "forbidden", "api key", "apikey")


@dataclass(frozen=True, slots=True)
class ParsedSnapshot:
    ticker: str
    price: float
    timestamp: float | None  # Unix seconds; None -> the cache uses time.time()
    baseline_price: float | None  # previous close, for daily change %


def _to_unix_seconds(raw: Any) -> float | None:
    """Normalize a Massive timestamp (s, ms, µs or ns since epoch) to seconds.

    Snapshot trade timestamps are nanoseconds; aggregate bars use milliseconds.
    Detecting by magnitude keeps us correct either way.
    """
    if raw is None:
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return None
    if value > 1e17:  # nanoseconds
        return value / 1e9
    if value > 1e14:  # microseconds
        return value / 1e6
    if value > 1e11:  # milliseconds
        return value / 1e3
    return value


def _positive(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if value > 0 else None


def parse_snapshot(snap: Any) -> ParsedSnapshot | None:
    """Extract (ticker, price, timestamp, baseline) from one TickerSnapshot.

    Price fallback chain: last trade -> current minute bar -> today's close ->
    previous day's close. Any sub-object may be None (pre-market, plan limits,
    new listings). Returns None if no usable price exists.
    """
    ticker = getattr(snap, "ticker", None)
    if not isinstance(ticker, str) or not ticker.strip():
        return None

    last_trade = getattr(snap, "last_trade", None)
    prev_day = getattr(snap, "prev_day", None)

    candidates = (
        getattr(last_trade, "price", None),
        getattr(getattr(snap, "min", None), "close", None),
        getattr(getattr(snap, "day", None), "close", None),
        getattr(prev_day, "close", None),
    )
    price = next((p for p in map(_positive, candidates) if p is not None), None)
    if price is None:
        return None

    timestamp = None
    for attr in ("sip_timestamp", "participant_timestamp", "timestamp"):
        timestamp = _to_unix_seconds(getattr(last_trade, attr, None))
        if timestamp is not None:
            break
    if timestamp is None:
        timestamp = _to_unix_seconds(getattr(snap, "updated", None))

    baseline = _positive(getattr(prev_day, "close", None))
    if baseline is None:
        todays_change = getattr(snap, "todays_change", None)
        if isinstance(todays_change, (int, float)) and not isinstance(todays_change, bool):
            baseline = _positive(price - todays_change)

    return ParsedSnapshot(ticker.strip().upper(), price, timestamp, baseline)


def _is_auth_error(exc: Exception) -> bool:
    """True if a BadResponse body says the key/plan is not allowed to use the endpoint."""
    text = str(exc).lower()
    return any(marker in text for marker in _AUTH_MARKERS)


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers for all tracked
    tickers in a single API call, then writes results to the PriceCache.

    The snapshot endpoint needs a Starter plan or above; a free key fails with
    NOT_AUTHORIZED, which start() surfaces as MarketDataAuthError so the caller
    can fall back to the simulator.

    Poll interval: 15s by default; 2-5s on paid tiers. Consecutive failures
    (429s, outages) double the delay up to MAX_BACKOFF_SECONDS.
    """

    mode = "massive"

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._extra_polls: set[asyncio.Task] = set()
        self._poll_lock = asyncio.Lock()
        self._client: RESTClient | None = None
        self._consecutive_failures = 0
        self._validated: set[str] = set()

    async def start(self, tickers: list[str]) -> None:
        """Poll once (raising MarketDataAuthError if the key/plan is rejected), then loop."""
        self._client = RESTClient(api_key=self._api_key)
        for ticker in tickers:
            self._track(ticker)

        await self._poll_once(raise_auth=True)  # populate the cache before serving

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(self._tickers),
            self._interval,
        )

    async def stop(self) -> None:
        tasks = [t for t in (self._task, *self._extra_polls) if t and not t.done()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._extra_polls.clear()
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._track(ticker):
            logger.info("Massive: added ticker %s", ticker)
            if self._client:
                # Don't make the user wait a full interval for the first price.
                task = asyncio.create_task(self._poll_once(), name=f"massive-poll-{ticker}")
                self._extra_polls.add(task)
                task.add_done_callback(self._extra_polls.discard)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = ticker.strip().upper()
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    async def validate_ticker(self, ticker: str) -> bool:
        """A ticker is valid only if the API returns data for it.

        Already-priced and previously validated tickers cost no API call.
        Transient failures raise PriceUnavailable rather than returning False.
        """
        ticker = ticker.strip().upper()
        if ticker in self._validated or (ticker in self._tickers and ticker in self._cache):
            return True
        if not self._client:
            raise PriceUnavailable("Massive client is not started")
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots, [ticker])
        except Exception as e:
            if _is_auth_error(e):
                raise MarketDataAuthError(str(e)) from e
            raise PriceUnavailable(f"Could not validate {ticker}: {e}") from e
        for snap in snapshots or []:
            parsed = parse_snapshot(snap)
            if parsed is not None and parsed.ticker == ticker:
                self._validated.add(ticker)  # negatives aren't cached: a symbol can start trading
                return True
        return False

    # --- Internal ---

    def _track(self, ticker: str) -> bool:
        """Add a normalized ticker to the list. Returns True if it was new."""
        ticker = normalize_ticker(ticker)
        if ticker in self._tickers:
            return False
        self._tickers.append(ticker)
        return True

    def _next_delay(self) -> float:
        """Poll interval, doubled per consecutive failure, capped."""
        if self._consecutive_failures == 0:
            return self._interval
        backoff = self._interval * 2**self._consecutive_failures
        return min(backoff, max(MAX_BACKOFF_SECONDS, self._interval))

    async def _poll_loop(self) -> None:
        """Poll on interval. First poll already happened in start()."""
        while True:
            await asyncio.sleep(self._next_delay())
            await self._poll_once()

    async def _poll_once(self, raise_auth: bool = False) -> None:
        """Execute one poll cycle: fetch snapshots, update cache."""
        async with self._poll_lock:
            if not self._tickers or not self._client:
                return
            requested = list(self._tickers)  # copy: the worker thread must not see mutations
            try:
                # The Massive RESTClient is synchronous — keep it off the event loop.
                snapshots = await asyncio.to_thread(self._fetch_snapshots, requested)
            except Exception as e:  # 401/403/429/5xx/network — keep last-known prices
                if raise_auth and _is_auth_error(e):
                    raise MarketDataAuthError(str(e)) from e
                self._consecutive_failures += 1
                log = logger.error if self._consecutive_failures <= 3 else logger.debug
                log("Massive poll failed (%d in a row): %s", self._consecutive_failures, e)
                return

            self._consecutive_failures = 0
            tracked = set(self._tickers)  # re-read: tickers may be removed mid-request
            seen: set[str] = set()
            for snap in snapshots or []:
                parsed = parse_snapshot(snap)
                if parsed is None:
                    logger.warning("Skipping unusable snapshot: %r", getattr(snap, "ticker", snap))
                    continue
                if parsed.ticker not in tracked:
                    continue  # removed while the request was in flight — don't resurrect it
                seen.add(parsed.ticker)
                self._cache.update(
                    ticker=parsed.ticker,
                    price=parsed.price,
                    timestamp=parsed.timestamp,
                    baseline_price=parsed.baseline_price,
                )

            if missing := tracked - seen:
                # Typically an invalid or delisted symbol: it simply never gets a price.
                logger.debug("Massive returned no data for: %s", ", ".join(sorted(missing)))

    def _fetch_snapshots(self, tickers: list[str]) -> list:
        """Synchronous call to the Massive REST API. Runs in a worker thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=tickers,
        )
