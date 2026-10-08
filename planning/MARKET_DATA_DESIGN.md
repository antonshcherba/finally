# Market Data Backend — Detailed Design

Implementation-ready design for FinAlly's market data subsystem: the unified data-source interface, the shared price cache, the GBM simulator, the Massive (Polygon.io) REST poller, the SSE stream, and how they plug into FastAPI and the rest of the backend.

Everything lives in `backend/app/market/`. All code below is complete module source, not pseudocode. The pure-Python parts (models, cache, simulator, Massive parsing and polling, factory, SSE generator) were run against the checks in §13 using a stub `massive` client.

### How this document relates to the existing code

A first version of this subsystem is already built (see `MARKET_DATA_SUMMARY.md`; the original design is in `archive/MARKET_DATA_DESIGN.md`). This document **supersedes the archived design**. It keeps the existing public API (`PriceUpdate`, `PriceCache`, `MarketDataSource`, `create_market_data_source`, `create_stream_router`) and fixes the problems found when the code was checked against the rest of `PLAN.md`. Lines marked **Δ** differ from what's in `backend/app/market/` today. §16 lists every change in one place.

---

## Table of Contents

1. [Requirements](#1-requirements)
2. [Architecture](#2-architecture)
3. [File Layout](#3-file-layout)
4. [Data Model — `models.py`](#4-data-model--modelspy)
5. [Price Cache — `cache.py`](#5-price-cache--cachepy)
6. [Unified Interface — `interface.py`](#6-unified-interface--interfacepy)
7. [Seed Data — `seed_prices.py`](#7-seed-data--seed_pricespy)
8. [GBM Simulator — `simulator.py`](#8-gbm-simulator--simulatorpy)
9. [Massive API Client — `massive_client.py`](#9-massive-api-client--massive_clientpy)
10. [Factory — `factory.py`](#10-factory--factorypy)
11. [SSE Stream — `stream.py`](#11-sse-stream--streampy)
12. [Backend Integration](#12-backend-integration)
13. [Testing](#13-testing)
14. [Error Handling & Edge Cases](#14-error-handling--edge-cases)
15. [Configuration Reference](#15-configuration-reference)
16. [Change List vs. Current Implementation](#16-change-list-vs-current-implementation)

---

## 1. Requirements

Traced from `PLAN.md`:

| # | Requirement | Where it's met |
|---|---|---|
| R1 | Two sources (simulator, Massive) behind one interface; downstream code doesn't know which is active | §6, §10 |
| R2 | `MASSIVE_API_KEY` set and non-empty → Massive; otherwise simulator | §10 |
| R3 | Simulator: GBM, ~500 ms ticks, correlated moves, occasional 2–5% events, realistic seed prices | §7, §8 |
| R4 | Massive: REST polling (not WebSocket), union of tracked tickers in one call, 15 s on free tier, 2–15 s on paid tiers | §9 |
| R5 | Shared in-memory cache holding latest price, previous price, timestamp per ticker | §5 |
| R6 | `GET /api/stream/prices` SSE, ~500 ms cadence; each event has ticker, price, previous price, timestamp, direction | §11 |
| R7 | Watchlist shows **daily change %** | §4 (`reference_price`, `day_change_percent`) **Δ** |
| R8 | Trades fill instantly at the current price, for any held ticker | §12.3 |
| R9 | Watchlist add/remove (REST or LLM) is reflected in the stream | §12.3 |
| R10 | EventSource auto-reconnect; header connection indicator | §11, §12.4 |
| R11 | Unit tests: valid simulator prices, GBM math, Massive parsing, interface conformance | §13 |

---

## 2. Architecture

```
                       ┌────────────────────────────────────────────┐
                       │      MarketDataSource (ABC, interface.py)  │
                       │ start · stop · add_ticker · remove_ticker  │
                       │                get_tickers                 │
                       └──────────────┬───────────────┬─────────────┘
                                      │               │
                  MASSIVE_API_KEY=""  │               │  MASSIVE_API_KEY="…"
                       ┌──────────────▼───┐   ┌───────▼──────────────────┐
                       │SimulatorDataSource│   │   MassiveDataSource      │
                       │ asyncio task,     │   │ asyncio task, every 15 s │
                       │ every 0.5 s:      │   │ to_thread(get_snapshot_  │
                       │ GBMSimulator.step │   │ all(tickers)) → parse    │
                       └─────────┬─────────┘   └───────────┬──────────────┘
                                 │  cache.update(...)      │
                                 ▼                         ▼
                       ┌────────────────────────────────────────────┐
                       │  PriceCache (cache.py) — threading.Lock    │
                       │  {ticker: PriceUpdate}  +  version counter │
                       └────┬──────────────────┬───────────────┬────┘
                            │ snapshot()       │ get_price()   │ get_all()
                            ▼                  ▼               ▼
                  GET /api/stream/prices   POST /api/portfolio/trade   GET /api/portfolio,
                  (SSE, every 0.5 s if     (fill price)                /api/watchlist,
                   version changed)                                    snapshots, LLM context
```

**Rules that keep it simple**

1. **Push, don't pull.** Sources write to the cache on their own schedule. Nothing downstream calls a source to get a price. SSE polls the cache at its own fixed cadence, so frontend updates are evenly spaced whichever source is active (good for sparklines).
2. **The cache is the only shared state.** One writer (the active source), many readers. Values are immutable `PriceUpdate`s, so readers can hold them without copying.
3. **Tracked set = watchlist ∪ held positions.** The source tracks every ticker the app needs a price for, not just the watchlist (§12.3).
4. **Tickers are canonical.** Uppercase, trimmed, via `normalize_ticker()`. Every entry point normalizes. **Δ** (today only the Massive source does).

**Timing at a glance**

| Loop | Interval | Writes cache? |
|---|---|---|
| Simulator tick | 0.5 s | yes, every tracked ticker |
| Massive poll | 15 s default (`MASSIVE_POLL_INTERVAL`), doubles per consecutive failure up to 120 s | yes, tickers returned by API |
| SSE push check | 0.5 s per client | no (reads); sends only when `version` changed |
| SSE heartbeat | 15 s of no changes | no; `: heartbeat` comment |

---

## 3. File Layout

```
backend/
  app/
    market/
      __init__.py          # Public re-exports
      models.py            # PriceUpdate, normalize_ticker
      cache.py             # PriceCache
      interface.py         # MarketDataSource ABC
      seed_prices.py       # Seed prices, GBM params, correlation groups (constants only)
      simulator.py         # GBMSimulator (math) + SimulatorDataSource (async wrapper)
      massive_client.py    # parse_snapshot() + MassiveDataSource
      factory.py           # create_market_data_source()
      stream.py            # create_stream_router(), generate_price_events()
      tracking.py          # sync_tracked_tickers()   Δ new
  tests/
    market/
      test_models.py  test_cache.py  test_simulator.py  test_simulator_source.py
      test_massive.py  test_factory.py  test_stream.py (Δ new)  test_tracking.py (Δ new)
```

Dependencies (already in `pyproject.toml`): `numpy` (simulator), `massive` (Massive client, imported at module top level, as it is a core dependency), `fastapi`. Dev: `pytest`, `pytest-asyncio` (`asyncio_mode = "auto"`).

**`backend/app/market/__init__.py`**

```python
"""Market data subsystem for FinAlly.

Public API:
    PriceUpdate               - Immutable price snapshot
    PriceCache                - Thread-safe in-memory price store
    MarketDataSource          - Abstract interface for data providers
    create_market_data_source - Factory: simulator or Massive, from env
    create_stream_router      - FastAPI router factory for the SSE endpoint
    sync_tracked_tickers      - Reconcile the source's tickers with a desired set
    normalize_ticker          - Canonical ticker form
"""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate, normalize_ticker
from .stream import create_stream_router
from .tracking import sync_tracked_tickers

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "MarketDataSource",
    "create_market_data_source",
    "create_stream_router",
    "sync_tracked_tickers",
    "normalize_ticker",
]
```

---

## 4. Data Model — `models.py`

`PriceUpdate` is the only type that leaves the market layer. It carries two notions of change:

- **Tick change** (`change`, `change_percent`, `direction`) is relative to the previous update and drives the green/red flash.
- **Daily change** (`day_change`, `day_change_percent`) is relative to `reference_price`: the simulator's session-open (seed) price, or Massive's previous close. This is the "daily change %" column in the watchlist. **Δ** (R7; today there's only tick change, which is ~0.01% and useless as a daily figure.)

Derived values are properties, so they can never disagree with `price`/`previous_price`.

```python
"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

Direction = Literal["up", "down", "flat"]


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    # Reference for "daily change": the simulator's session-open price, or
    # Massive's previous close. None if unknown (day_change* fall back to 0).
    reference_price: float | None = None

    @property
    def change(self) -> float:
        """Absolute change since the previous update (tick-to-tick)."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percent change since the previous update (tick-to-tick)."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> Direction:
        """'up', 'down', or 'flat' relative to the previous update. Drives the UI flash."""
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    @property
    def day_change(self) -> float:
        """Absolute change since the reference (session open / previous close)."""
        if not self.reference_price:
            return 0.0
        return round(self.price - self.reference_price, 4)

    @property
    def day_change_percent(self) -> float:
        """Percent change since the reference. Shown as 'daily change %' in the watchlist."""
        if not self.reference_price:
            return 0.0
        return round((self.price - self.reference_price) / self.reference_price * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission. The single wire format."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "reference_price": self.reference_price,
            "day_change": self.day_change,
            "day_change_percent": self.day_change_percent,
        }


def normalize_ticker(ticker: str) -> str:
    """Canonical ticker form used everywhere in the market layer: ' aapl ' -> 'AAPL'."""
    return ticker.strip().upper()
```

**Design notes**

- `frozen=True, slots=True`: immutable, cheap value objects shared across tasks and threads.
- `reference_price` defaults to `None`, so existing constructors `PriceUpdate(ticker, price, previous_price, timestamp)` keep working.
- `to_dict()` is the single wire format, used by SSE and by REST responses that embed prices.

---

## 5. Price Cache — `cache.py`

```python
"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Thread-safe in-memory cache of the latest price for each ticker.

    Writers: SimulatorDataSource or MassiveDataSource (one at a time).
    Readers: SSE streaming endpoint, portfolio valuation, trade execution.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version: int = 0  # Monotonic; bumped on every update AND remove

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        reference_price: float | None = None,
    ) -> PriceUpdate:
        """Record a new price for a ticker and return the resulting PriceUpdate.

        - First update for a ticker: previous_price == price (direction 'flat').
        - reference_price=None keeps the previously stored reference; if there is
          none yet, the first price seen becomes the reference.
        """
        with self._lock:
            ts = time.time() if timestamp is None else timestamp
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price
            if reference_price is None:
                reference_price = prev.reference_price if prev else price

            update = PriceUpdate(
                ticker=ticker,
                price=round(float(price), 2),
                previous_price=round(float(previous_price), 2),
                timestamp=ts,
                reference_price=round(float(reference_price), 2),
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        """Latest update for one ticker, or None if unknown."""
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        """Convenience: just the price float, or None."""
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy of all current prices (values are immutable)."""
        with self._lock:
            return dict(self._prices)

    def snapshot(self) -> tuple[int, dict[str, PriceUpdate]]:
        """Atomically read (version, prices) — what the SSE loop uses."""
        with self._lock:
            return self._version, dict(self._prices)

    def remove(self, ticker: str) -> None:
        """Drop a ticker. Bumps the version so SSE clients see the removal promptly."""
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
```

**Why `threading.Lock`, not `asyncio.Lock`:** the Massive poll runs its HTTP call in a worker thread (`asyncio.to_thread`). Writes happen back on the event loop today, but the lock makes the cache safe no matter which thread calls it, and costs nothing at this scale (critical sections are a dict lookup and an assignment).

**Why a version counter:** the SSE loop checks every 0.5 s. With Massive updating every 15 s, re-sending identical payloads 30× is pointless. `snapshot()` returns `(version, prices)` atomically, so a client never pairs a new version with old prices.

**Δ changes vs. current code**

| Change | Why |
|---|---|
| `timestamp is None` instead of `timestamp or time.time()` | `0.0` is a valid value and shouldn't silently become "now" |
| `remove()` bumps `version` (only if something was removed) | With Massive, a removed ticker otherwise lingers in SSE frames for up to 15 s |
| `version` read under the lock; new `snapshot()` | Consistency; safe on free-threaded Python 3.13t+ |
| `reference_price` parameter, sticky across updates | Daily change % (R7) |
| `float(...)` before rounding | `cache.update("X", 190)` serializes as `190.0`, not `190`; the frontend sees consistent number formatting |

---

## 6. Unified Interface — `interface.py`

```python
"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push price updates into a shared PriceCache on their own
    schedule. Downstream code never asks the source for prices — it reads the cache.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL", ...])
        await source.add_ticker("TSLA")
        await source.remove_ticker("GOOGL")
        await source.stop()

    All ticker arguments are normalized with normalize_ticker() by the implementation.
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing prices. Seeds the cache before returning when possible.
        Must be called exactly once."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task. Idempotent. No cache writes after it returns."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking a ticker. No-op if already tracked."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and drop it from the cache. No-op if unknown."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers, in insertion order."""
```

**Contract details both implementations honour**

| Method | Simulator | Massive |
|---|---|---|
| `start(tickers)` | Builds `GBMSimulator`, seeds cache with seed prices, starts 0.5 s loop | Creates `RESTClient`, does one poll synchronously (cache filled before app serves), starts loop |
| `add_ticker(t)` | Priced **immediately** (seed or random $50–300) | Priced on the **next poll** (≤ interval) |
| `remove_ticker(t)` | Removed from sim + cache | Removed from list + cache; in-flight poll results for it are discarded **Δ** |
| `stop()` | Cancel + await task; idempotent | Same; drops client |
| `get_tickers()` | Sim's insertion-ordered list | Own list |

Downstream code must tolerate `cache.get_price(t) is None` for a just-added ticker (Massive), see §14.

---

## 7. Seed Data — `seed_prices.py`

Unchanged from the current implementation. Constants only.

```python
"""Seed prices and per-ticker parameters for the market simulator."""

# Realistic starting prices for the default watchlist (as of project creation)
SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00,
    "GOOGL": 175.00,
    "MSFT": 420.00,
    "AMZN": 185.00,
    "TSLA": 250.00,
    "NVDA": 800.00,
    "META": 500.00,
    "JPM": 195.00,
    "V": 280.00,
    "NFLX": 600.00,
}

# Per-ticker GBM parameters
# sigma: annualized volatility (higher = more price movement)
# mu: annualized drift / expected return
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},  # High volatility
    "NVDA": {"sigma": 0.40, "mu": 0.08},  # High volatility, strong drift
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},  # Low volatility (bank)
    "V": {"sigma": 0.17, "mu": 0.04},  # Low volatility (payments)
    "NFLX": {"sigma": 0.35, "mu": 0.05},
}

# Default parameters for tickers not in the list above (dynamically added)
DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

# Correlation groups for the simulator's Cholesky decomposition
# Tickers in the same group have higher intra-group correlation
CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

# Correlation coefficients
INTRA_TECH_CORR = 0.6  # Tech stocks move together
INTRA_FINANCE_CORR = 0.5  # Finance stocks move together
CROSS_GROUP_CORR = 0.3  # Between sectors / unknown tickers
TSLA_CORR = 0.3  # TSLA does its own thing
```

Volatility is ordered the way real names behave: V/JPM lowest, TSLA highest. NVDA gets the strongest drift.

---

## 8. GBM Simulator — `simulator.py`

### 8.1 The math

Per ticker per tick:

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z )
```

- `μ` (`mu`): annualized drift; `σ` (`sigma`): annualized volatility.
- `dt` = one tick as a fraction of a trading year: `0.5 s / (252 days × 6.5 h × 3600 s) = 0.5 / 5,896,800 ≈ 8.48e-8`.
- `−σ²/2` is the Itô correction, so the *expected* price grows at `μ`, not `μ + σ²/2`.
- `exp(·) > 0`, so prices can never go negative.

**Correlated draws.** Build the correlation matrix `C` from sector rules (tech–tech 0.6, finance–finance 0.5, anything with TSLA 0.3, everything else 0.3), factor `C = L·Lᵀ` (Cholesky), and each tick draw `z ~ N(0, I)` and use `Z = L·z`. Then `Cov(Z) = L·I·Lᵀ = C`. This matrix is always positive-definite: it's `0.3·J + 0.7·I`, plus non-negative block bumps for the sectors, so Cholesky never fails, even with dozens of unknown tickers (the 70-ticker case is tested in §13).

**Shock events.** Each tick each ticker has probability `p = 0.001` of an extra multiplicative jump of ±2–5%.

### 8.2 Calibration: what the user will see

| Quantity | Value | Comment |
|---|---|---|
| Per-tick σ for AAPL | `0.22·√8.48e-8 ≈ 0.0064%` ≈ **$0.012** | Most ticks move a cent or two. Prices are rounded to cents, so some ticks are "flat" |
| Per-tick σ for TSLA | `0.50·√dt ≈ 0.0146%` ≈ $0.04 | Visibly livelier |
| Shock rate | 2 ticks/s × 10 tickers × 0.001 = **one every ~50 s** somewhere; per ticker every ~8 min | The "drama" |
| Over a 10-minute demo | ~1 shock per ticker, GBM σ ≈ 0.2% | Feels like a real active market |

⚠️ Over **hours**, shocks dominate (about 47 per ticker per simulated 6.5 h day, far more variance than σ). That's intended for a demo. If long-running sessions look too wild, lower `event_probability` (e.g. `0.0002`). It's a constructor parameter.

Verified empirically (§13): a tech pair's log returns correlate at 0.598 (target 0.6), tech vs. finance at 0.300 (target 0.3), and AAPL per-tick σ is within 1% of `σ·√dt`.

### 8.3 Source

```python
"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import logging
import math
import random

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .models import normalize_ticker
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
)

logger = logging.getLogger(__name__)


class GBMSimulator:
    """Correlated Geometric Brownian Motion price generator (pure, synchronous).

        S(t+dt) = S(t) * exp((mu - sigma^2/2) * dt + sigma * sqrt(dt) * Z)

    Z is a vector of correlated standard normals: Z = L @ z, where L is the
    Cholesky factor of the sector correlation matrix and z ~ N(0, I).
    """

    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR  # one 500ms tick ≈ 8.48e-8 years

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        self._rng = np.random.default_rng(seed)  # normals for diffusion
        self._py_rng = random.Random(seed)  # events + unknown-ticker seed prices

        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None

        for ticker in tickers:
            self._add_ticker_internal(normalize_ticker(ticker))
        self._rebuild_cholesky()

    # --- Public API ---

    def step(self) -> dict[str, float]:
        """Advance every ticker one tick. Returns {ticker: price rounded to cents}."""
        n = len(self._tickers)
        if n == 0:
            return {}

        z = self._rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z

        sqrt_dt = math.sqrt(self._dt)
        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]

            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * sqrt_dt * z[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            # Rare shock event: a sudden 2–5% jump in either direction, for drama.
            if self._py_rng.random() < self._event_prob:
                shock = self._py_rng.uniform(0.02, 0.05) * self._py_rng.choice((-1, 1))
                self._prices[ticker] *= 1 + shock
                logger.debug("Shock event on %s: %+.1f%%", ticker, shock * 100)

            result[ticker] = round(self._prices[ticker], 2)
        return result

    def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        return self._prices.get(normalize_ticker(ticker))

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internals ---

    def _add_ticker_internal(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        self._tickers.append(ticker)
        self._prices[ticker] = SEED_PRICES.get(ticker) or round(
            self._py_rng.uniform(50.0, 300.0), 2
        )
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """Rebuild L for the current ticker set. O(n^3) via numpy, fine for n < 100."""
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho
        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        tech = CORRELATION_GROUPS["tech"]
        finance = CORRELATION_GROUPS["finance"]
        if t1 == "TSLA" or t2 == "TSLA":  # TSLA does its own thing
            return TSLA_CORR
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR  # cross-sector and unknown tickers


class SimulatorDataSource(MarketDataSource):
    """MarketDataSource that steps a GBMSimulator every `update_interval` seconds."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._seed = seed
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(
            tickers=tickers, event_probability=self._event_prob, seed=self._seed
        )
        # Seed the cache so the first SSE push and first trade have prices.
        # The seed price becomes each ticker's reference ("session open").
        for ticker in self._sim.get_tickers():
            self._cache.update(ticker=ticker, price=self._sim.get_price(ticker))
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(self._sim.get_tickers()))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        if not self._sim:
            return
        ticker = normalize_ticker(ticker)
        self._sim.add_ticker(ticker)
        if ticker not in self._cache:  # seed immediately; don't clobber an existing entry
            self._cache.update(ticker=ticker, price=self._sim.get_price(ticker))
        logger.info("Simulator: added %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        logger.info("Simulator: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker=ticker, price=price)
            except Exception:
                logger.exception("Simulator step failed")  # keep the feed alive
            await asyncio.sleep(self._interval)
```

**Δ changes vs. current code**

- **`seed` parameter** (both classes): uses a private `numpy.random.Generator` and a private `random.Random` instead of global RNG state. Deterministic tests become possible, and other code that seeds the global RNG can't interfere.
- **Ticker normalization** in `add_ticker`, `remove_ticker`, `get_price`, and the constructor.
- **`add_ticker` doesn't overwrite an existing cache entry** (e.g. a ticker re-added before the next tick keeps its `previous_price`/reference).
- `start()` seeds the cache from `sim.get_tickers()` (normalized, de-duplicated) instead of the raw input list.
- Unknown-ticker seed prices are rounded to cents.

### 8.4 Why `step()` runs on the event loop

For n ≤ ~50 tickers a step is a few microseconds of numpy plus a Python loop. Offloading it to a thread would cost more than it saves. If the ticker count ever grows into the thousands, vectorize the per-ticker loop (`prices *= np.exp(drift_vec + sigma_vec * sqrt_dt * z)`) before reaching for threads.

---

## 9. Massive API Client — `massive_client.py`

### 9.1 The endpoint

One request fetches every tracked ticker, which is what makes a 5 req/min free tier workable:

```
GET https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,TSLA
Authorization: Bearer <MASSIVE_API_KEY>        (the client adds this)
```

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key=api_key)
snapshots = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,
    tickers=["AAPL", "MSFT", "TSLA"],
)  # -> list[TickerSnapshot]
```

Raw JSON shape per ticker (abridged; the client maps it to attributes):

```json
{
  "ticker": "AAPL",
  "todaysChange": -4.54, "todaysChangePerc": -3.5,
  "updated": 1675190399500000000,
  "day":     {"o": 129.61, "h": 130.15, "l": 125.07, "c": 125.07, "v": 111237700},
  "min":     {"o": 125.1, "h": 125.12, "l": 125.05, "c": 125.07, "v": 2100},
  "prevDay": {"o": 128.0, "h": 130.0, "l": 127.5, "c": 129.61, "v": 98000000},
  "lastTrade": {"p": 125.07, "s": 100, "x": 4, "t": 1675190399000000000},
  "lastQuote": {"p": 125.06, "P": 125.08, "s": 5, "S": 10, "t": 1675190399500000000}
}
```

Python attribute mapping used below: `snap.ticker`, `snap.last_trade.price`, `snap.last_trade.sip_timestamp` (raw `t`), `snap.min.close`, `snap.day.close`, `snap.prev_day.close`, `snap.todays_change`, `snap.updated`.

> **Verify on first use.** This environment couldn't install `massive` (no PyPI access), so the attribute names above come from the Polygon client the `massive` package is derived from, not from the installed package. Before relying on them, run
> `uv run python -c "from massive.rest.models import TickerSnapshot, LastTrade; help(LastTrade)"`.
> The parser below is written so that a naming difference degrades gracefully instead of crashing. It tries several attribute names, and if none resolves it falls back to "now" for the timestamp. But it's worth one check.

**Δ The current code reads `snap.last_trade.timestamp / 1000` (assumes milliseconds).** In the snapshot payload the trade time `t` is in **nanoseconds**, and the Polygon-style model exposes it as `sip_timestamp`. If `.timestamp` doesn't exist, every snapshot raises `AttributeError` and is skipped, so no prices would ever reach the cache. In that case the existing unit tests still pass, because their `MagicMock` snapshots define `.timestamp`. The new parser detects the unit by magnitude and tries several attribute names.

### 9.2 Parsing rules

| Field | Source, in priority order | Why |
|---|---|---|
| price | `last_trade.price` → `min.close` → `day.close` → `prev_day.close` (first value > 0) | `last_trade` can be missing (plan limits, pre-market, thin names); `day` resets to zeros at the open |
| timestamp | `last_trade.sip_timestamp` / `.timestamp` / `.participant_timestamp` → `snap.updated` → `None` (cache uses now) | Units normalized s/ms/µs/ns by magnitude |
| reference_price | `prev_day.close` → `price − todays_change` → `None` | Daily change % (R7) |

### 9.3 Source

```python
"""Massive (formerly Polygon.io) REST poller for real market data."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

from massive import RESTClient
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource
from .models import normalize_ticker

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class ParsedSnapshot:
    ticker: str
    price: float
    timestamp: float | None  # Unix seconds; None -> cache uses time.time()
    reference_price: float | None  # previous close, for daily change %


def _to_unix_seconds(raw: Any) -> float | None:
    """Normalize a Massive timestamp (s, ms, µs or ns since epoch) to seconds.

    Snapshot trade timestamps are nanoseconds; some endpoints use milliseconds.
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
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def parse_snapshot(snap: Any) -> ParsedSnapshot | None:
    """Extract (ticker, price, timestamp, reference) from one TickerSnapshot.

    Price fallback chain: last trade → current minute bar close → today's close
    → previous day's close. Returns None if no usable price exists.
    """
    ticker = getattr(snap, "ticker", None)
    if not ticker:
        return None

    # Any of these sub-objects may be None (pre-market, plan limits, new listings).
    # getattr(None, name, None) is None, so the chains below never raise.
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
    if last_trade is not None:
        for attr in ("sip_timestamp", "timestamp", "participant_timestamp"):
            timestamp = _to_unix_seconds(getattr(last_trade, attr, None))
            if timestamp is not None:
                break
    if timestamp is None:
        timestamp = _to_unix_seconds(getattr(snap, "updated", None))

    reference = _positive(getattr(prev_day, "close", None))
    if reference is None:
        todays_change = getattr(snap, "todays_change", None)
        if isinstance(todays_change, (int, float)):
            reference = _positive(price - todays_change)

    return ParsedSnapshot(normalize_ticker(ticker), price, timestamp, reference)


class MassiveDataSource(MarketDataSource):
    """Polls the Massive snapshot endpoint for all tracked tickers in ONE request.

    GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,...

    Free tier is 5 req/min, so the default interval is 15s. Paid tiers: 2–5s.
    """

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
        self._client: RESTClient | None = None
        self._consecutive_failures = 0

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers))
        await self._poll_once()  # populate the cache before the app serves requests
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(self._tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("Massive: added %s (priced on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internal ---

    def _next_delay(self) -> float:
        """Poll interval, doubled per consecutive failure (429s, outages), capped."""
        if self._consecutive_failures == 0:
            return self._interval
        backoff = self._interval * 2 ** self._consecutive_failures
        return min(backoff, max(MAX_BACKOFF_SECONDS, self._interval))

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._next_delay())
            await self._poll_once()

    async def _poll_once(self) -> None:
        if not self._tickers or not self._client:
            return
        requested = list(self._tickers)  # copy: the worker thread must not see mutations
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots, requested)
        except Exception as e:  # 401/403/429/5xx/network — keep last-known prices
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
                reference_price=parsed.reference_price,
            )

        if missing := tracked - seen:
            # Typical cause: an invalid/delisted symbol. It simply never gets a price.
            logger.debug("Massive returned no data for: %s", ", ".join(sorted(missing)))

    def _fetch_snapshots(self, tickers: list[str]) -> list:
        """Blocking HTTP call — always run via asyncio.to_thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=tickers,
        )
```

**Δ changes vs. current code**

| Change | Why |
|---|---|
| `parse_snapshot()` as a pure function, with price/timestamp fallbacks | Correct units, survives missing sub-objects, unit-testable with `SimpleNamespace` |
| `_fetch_snapshots(tickers)` receives a **copy** of the list | The worker thread never iterates a list the event loop is mutating |
| Results for tickers removed mid-request are dropped | Otherwise a `remove_ticker` during a 300 ms HTTP call is undone when the response lands: the ticker reappears in the cache and the SSE stream until restart |
| Exponential backoff on consecutive failures (×2 each, capped at max(120 s, interval)); log level drops after 3 failures | Repeated 429s on the free tier stop hammering the API and the log |
| `reference_price` written to the cache | Daily change % |
| `start()` de-duplicates and normalizes tickers | Same canonical form as the simulator |

### 9.4 Rate-limit budget

| Tier | Limit | Recommended `MASSIVE_POLL_INTERVAL` | Requests/min |
|---|---|---|---|
| Free | 5/min | 15 (default) | 4 |
| Starter/Developer | unlimited (stay < 100/s) | 5 | 12 |
| Advanced+ | unlimited | 2 | 30 |

Only the poller calls the API. Watchlist adds, trades and SSE never trigger extra requests, so the budget is fixed by the interval alone. That's why `add_ticker` waits for the next poll rather than fetching on demand.

---

## 10. Factory — `factory.py`

```python
"""Select the market data source from environment variables."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)

DEFAULT_MASSIVE_POLL_INTERVAL = 15.0  # free tier: 5 requests/minute


def _poll_interval_from_env() -> float:
    raw = os.environ.get("MASSIVE_POLL_INTERVAL", "").strip()
    if not raw:
        return DEFAULT_MASSIVE_POLL_INTERVAL
    try:
        value = float(raw)
    except ValueError:
        logger.warning("Invalid MASSIVE_POLL_INTERVAL=%r; using %.0fs", raw, DEFAULT_MASSIVE_POLL_INTERVAL)
        return DEFAULT_MASSIVE_POLL_INTERVAL
    return max(value, 1.0)


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """MASSIVE_API_KEY set and non-empty → MassiveDataSource, else SimulatorDataSource.

    Returns an unstarted source; the caller must `await source.start(tickers)`.
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        interval = _poll_interval_from_env()
        logger.info("Market data source: Massive API (poll every %.1fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)

    logger.info("Market data source: GBM simulator")
    return SimulatorDataSource(price_cache=price_cache)
```

**Δ** `MASSIVE_POLL_INTERVAL` is new (optional; default 15; floor 1 s; invalid values log a warning and fall back). Add it to `.env.example`:

```bash
# Optional: seconds between Massive polls (free tier: keep >= 12; paid: 2-5)
MASSIVE_POLL_INTERVAL=15
```

---

## 11. SSE Stream — `stream.py`

```python
"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

PUSH_INTERVAL = 0.5  # seconds between cache checks
HEARTBEAT_INTERVAL = 15.0  # comment line when idle, keeps proxies from closing the socket
RETRY_MS = 1000  # EventSource reconnect delay


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Build a fresh router per call (no module-level router → safe to call in tests)."""
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            generate_price_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # disable nginx buffering if proxied
            },
        )

    return router


def format_sse(data: dict, event_id: int | None = None) -> str:
    """One SSE frame. `id:` lets a reconnecting EventSource send Last-Event-ID."""
    head = f"id: {event_id}\n" if event_id is not None else ""
    return f"{head}data: {json.dumps(data, separators=(',', ':'))}\n\n"


async def generate_price_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = PUSH_INTERVAL,
    heartbeat: float = HEARTBEAT_INTERVAL,
) -> AsyncGenerator[str, None]:
    """Yield a full price snapshot whenever the cache version changes.

    - First frame is always sent immediately (if the cache has data), so a new or
      reconnecting client renders without waiting for the next tick.
    - Each frame is the COMPLETE set of tracked tickers, so removals are implicit:
      a ticker missing from the frame is no longer tracked.
    """
    yield f"retry: {RETRY_MS}\n\n"

    client = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client)
    last_version = -1
    idle = 0.0
    try:
        while not await request.is_disconnected():
            version, prices = price_cache.snapshot()
            if version != last_version:
                last_version = version
                idle = 0.0
                yield format_sse(
                    {ticker: update.to_dict() for ticker, update in prices.items()},
                    event_id=version,
                )
            else:
                idle += interval
                if idle >= heartbeat:
                    idle = 0.0
                    yield ": heartbeat\n\n"
            await asyncio.sleep(interval)
    finally:  # runs on disconnect and on server-side cancellation alike
        logger.info("SSE client disconnected: %s", client)
```

### 11.1 Wire format

```
retry: 1000

id: 4182
data: {"AAPL":{"ticker":"AAPL","price":190.52,"previous_price":190.49,"timestamp":1791460261.11,"change":0.03,"change_percent":0.0157,"direction":"up","reference_price":190.0,"day_change":0.52,"day_change_percent":0.2737},"GOOGL":{...},...}

: heartbeat

```

- One `message` event per change, carrying the **full** map of tracked tickers. A ticker absent from a frame is no longer tracked. Clients replace their price map with each frame rather than merging. Full frames are ~250 bytes × 10 tickers ≈ 2.5 KB every 0.5 s, which is negligible. Diffing would add state and bugs for no visible benefit.
- `id:` is the cache version. It's informational (we always send the full state on connect, so `Last-Event-ID` needs no handling).
- `retry: 1000` → EventSource reconnects 1 s after a drop.
- `: heartbeat` comments (ignored by EventSource) every 15 s of silence keep idle connections alive through proxies and load balancers. This matters mostly in Massive mode, where data changes every 15 s.
- The first data frame is sent immediately on connect. If the cache is empty (empty watchlist) it's `{}`, which tells the client "nothing tracked".

### 11.2 Δ changes vs. current code

| Change | Why |
|---|---|
| Router created **inside** `create_stream_router` | The current module-level `router` registers `/prices` again on every call (duplicate routes in tests) |
| `price_cache.snapshot()` (atomic version + data) | No mismatched version/data reads |
| Heartbeat comment | Idle-connection survival |
| `id:` field, compact JSON separators | Debuggability, smaller frames |
| `generate_price_events` is public; no swallowed `CancelledError` (cleanup in `finally`) | Directly testable (§13); cancellation propagates as asyncio expects |

---

## 12. Backend Integration

### 12.1 Lifespan wiring — `backend/app/main.py`

Routes must be registered before the app serves requests, but sources must be started inside `lifespan` (they need a running event loop). So the **cache is created at import time** and handed to `create_stream_router`, and the **source is created and started in `lifespan`**:

```python
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.market import PriceCache, create_market_data_source, create_stream_router

price_cache = PriceCache()  # shared, process-wide; empty until the source starts


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Database first: schema + seed data exist before we read the watchlist.
    init_db()  # app.db — lazily creates tables and seeds defaults

    # 2. Pick and start the source; tracked set = watchlist ∪ open positions (12.3).
    source = create_market_data_source(price_cache)
    app.state.price_cache = price_cache
    app.state.market_source = source
    await source.start(get_tracked_tickers())  # app.db

    # 3. Other background tasks (portfolio snapshot every 30 s) start here.
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)
app.include_router(create_stream_router(price_cache))  # GET /api/stream/prices
# app.include_router(portfolio_router); app.include_router(watchlist_router); ...

# Static Next.js export LAST so /api/* routes win.
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
```

Don't call `app.include_router` inside `lifespan`, as the archived design did. It relies on registering routes after startup has begun.

### 12.2 Dependencies for route handlers

Resolve from `request.app.state`, not a module-level `app` reference, so tests can build their own app:

```python
# backend/app/deps.py
from fastapi import Request

from app.market import MarketDataSource, PriceCache


def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache


def get_market_source(request: Request) -> MarketDataSource:
    return request.app.state.market_source
```

### 12.3 Ticker tracking: watchlist ∪ positions

A position must keep getting prices even after its ticker leaves the watchlist (portfolio value, heatmap, P&L), and a ticker bought by the LLM that was never on the watchlist needs a price at all. Rather than special-casing each route, every mutation ends with one idempotent reconcile:

**`backend/app/market/tracking.py`** (Δ new)

```python
"""Keep the data source's ticker set equal to what the app needs prices for."""

from __future__ import annotations

from collections.abc import Iterable

from .interface import MarketDataSource
from .models import normalize_ticker


async def sync_tracked_tickers(source: MarketDataSource, desired: Iterable[str]) -> None:
    """Add missing tickers and remove unneeded ones. Idempotent.

    `desired` is watchlist tickers ∪ tickers with quantity > 0.
    """
    want = {normalize_ticker(t) for t in desired}
    have = set(source.get_tickers())
    for ticker in sorted(want - have):
        await source.add_ticker(ticker)
    for ticker in sorted(have - want):
        await source.remove_ticker(ticker)
```

The DB side (owned by the backend agent) supplies the desired set:

```sql
SELECT ticker FROM watchlist WHERE user_id = :uid
UNION
SELECT ticker FROM positions WHERE user_id = :uid AND quantity > 0;
```

**Call sites**

```python
# POST /api/watchlist
ticker = normalize_ticker(payload.ticker)
db.add_watchlist(ticker)
await sync_tracked_tickers(source, db.get_tracked_tickers())
return {"ticker": ticker, "price": cache.get_price(ticker)}  # None until first Massive poll


# DELETE /api/watchlist/{ticker}
db.remove_watchlist(normalize_ticker(ticker))
await sync_tracked_tickers(source, db.get_tracked_tickers())  # stays tracked if held


# POST /api/portfolio/trade
ticker = normalize_ticker(req.ticker)
if ticker not in source.get_tickers():
    await source.add_ticker(ticker)  # simulator: priced now; Massive: next poll
price = cache.get_price(ticker)
if price is None:
    raise HTTPException(400, f"No price available for {ticker} yet — try again in a few seconds.")
execute_trade(ticker, req.side, req.quantity, price)  # validation + DB writes
record_portfolio_snapshot()
await sync_tracked_tickers(source, db.get_tracked_tickers())  # drop if sold out & unwatched
```

The LLM chat flow executes `trades` and `watchlist_changes` through these same functions, then calls `sync_tracked_tickers` once at the end.

> Note: the SSE stream therefore includes position-only tickers that aren't on the watchlist. The frontend renders the **watchlist panel** from `GET /api/watchlist` (the list of tickers) and takes **prices** for everything from the stream.

### 12.4 Frontend contract

TypeScript types mirroring `PriceUpdate.to_dict()`:

```ts
export type Direction = "up" | "down" | "flat";

export interface PriceUpdate {
  ticker: string;
  price: number;
  previous_price: number;
  timestamp: number;          // Unix seconds (float)
  change: number;             // vs previous tick
  change_percent: number;     // vs previous tick
  direction: Direction;       // drives flash animation
  reference_price: number | null;
  day_change: number;         // vs session open / previous close
  day_change_percent: number; // watchlist "daily change %"
}

export type PriceFrame = Record<string, PriceUpdate>;
```

Minimal hook:

```ts
import { useEffect, useRef, useState } from "react";

type Status = "connected" | "reconnecting" | "disconnected";

export function usePriceStream() {
  const [prices, setPrices] = useState<PriceFrame>({});
  const [status, setStatus] = useState<Status>("reconnecting");
  const history = useRef<Record<string, { t: number; p: number }[]>>({}); // sparklines

  useEffect(() => {
    const es = new EventSource("/api/stream/prices");
    es.onopen = () => setStatus("connected");
    es.onmessage = (e) => {
      const frame: PriceFrame = JSON.parse(e.data);
      for (const u of Object.values(frame)) {
        const h = (history.current[u.ticker] ??= []);
        if (h.at(-1)?.t !== u.timestamp) h.push({ t: u.timestamp, p: u.price });
        if (h.length > 600) h.shift(); // ~5 min at 2 Hz
      }
      setPrices(frame); // replace, don't merge: absent ticker = untracked
    };
    es.onerror = () =>
      setStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
    return () => es.close();
  }, []);

  return { prices, status, history: history.current };
}
```

Flash: when `direction !== "flat"` and `price` differs from the last rendered value, add `flash-up`/`flash-down` for ~500 ms.

---

## 13. Testing

All under `backend/tests/market/`; run with `uv run --extra dev pytest -v`. The existing model, cache, simulator and factory tests still pass after the changes in §16. The `MagicMock`-based tests in `test_massive.py` (`test_timestamp_conversion`, `test_malformed_snapshot_skipped`, and those that assert on parsed values) **must be rewritten** as in 13.4. A `MagicMock` auto-creates `sip_timestamp`/`min.close`, and `float(MagicMock())` is `1.0`, so they'd parse garbage. New and changed tests:

### 13.1 Models & cache

```python
from app.market.cache import PriceCache
from app.market.models import PriceUpdate


def test_day_change_uses_reference():
    u = PriceUpdate("AAPL", 191.0, 190.0, 1.0, reference_price=200.0)
    assert u.direction == "up" and u.change == 1.0
    assert u.day_change == -9.0 and u.day_change_percent == -4.5


def test_reference_is_sticky_and_defaults_to_first_price():
    c = PriceCache()
    assert c.update("AAPL", 190.0).reference_price == 190.0
    assert c.update("AAPL", 191.0).reference_price == 190.0


def test_zero_timestamp_is_respected():
    assert PriceCache().update("AAPL", 1.0, timestamp=0.0).timestamp == 0.0


def test_remove_bumps_version_only_when_present():
    c = PriceCache()
    c.update("AAPL", 190.0)
    v = c.version
    c.remove("AAPL")
    c.remove("AAPL")
    assert c.version == v + 1


def test_int_price_serializes_as_float():
    assert isinstance(PriceCache().update("AAPL", 190).to_dict()["price"], float)
```

### 13.2 Simulator math

```python
import math

import numpy as np

from app.market.seed_prices import SEED_PRICES
from app.market.simulator import GBMSimulator

DEFAULT_TEN = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


def test_seeded_runs_are_deterministic():
    a, b = GBMSimulator(["AAPL", "ZZZZ"], seed=42), GBMSimulator(["AAPL", "ZZZZ"], seed=42)
    assert [a.step() for _ in range(50)] == [b.step() for _ in range(50)]


def test_full_default_set_and_many_unknowns_factor():
    assert GBMSimulator(DEFAULT_TEN)._cholesky.shape == (10, 10)
    GBMSimulator([f"T{i}" for i in range(60)] + DEFAULT_TEN)  # must not raise


def test_prices_stay_positive():
    sim = GBMSimulator(DEFAULT_TEN, seed=1)
    for _ in range(5_000):
        assert all(p > 0 for p in sim.step().values())


def _log_returns(sim, tickers, n):
    out = []
    for _ in range(n):
        before = dict(sim._prices)
        sim.step()
        out.append([math.log(sim._prices[t] / before[t]) for t in tickers])
    return np.array(out)


def test_correlation_structure():
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], event_probability=0, seed=7)
    r = np.corrcoef(_log_returns(sim, ["AAPL", "MSFT", "JPM"], 20_000).T)
    assert abs(r[0, 1] - 0.6) < 0.03  # tech-tech
    assert abs(r[0, 2] - 0.3) < 0.03  # cross-sector


def test_per_tick_volatility_matches_sigma_sqrt_dt():
    sim = GBMSimulator(["AAPL"], event_probability=0, seed=7)
    std = _log_returns(sim, ["AAPL"], 20_000).std()
    assert abs(std / (0.22 * math.sqrt(GBMSimulator.DEFAULT_DT)) - 1) < 0.05


def test_tickers_are_normalized():
    sim = GBMSimulator([" aapl "])
    assert sim.get_tickers() == ["AAPL"] and sim.get_price("aapl") == SEED_PRICES["AAPL"]
```

### 13.3 Simulator source

```python
import asyncio

from app.market.cache import PriceCache
from app.market.simulator import SimulatorDataSource


async def test_lifecycle_add_remove_and_no_writes_after_stop():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=3)
    await src.start(["AAPL", "googl"])
    assert set(cache.get_all()) == {"AAPL", "GOOGL"}  # seeded before first tick

    await src.add_ticker(" tsla ")
    assert "TSLA" in cache  # priced immediately
    await src.remove_ticker("tsla")
    await asyncio.sleep(0.05)
    assert "TSLA" not in cache  # loop didn't resurrect it

    await src.stop()
    await src.stop()  # idempotent
    v = cache.version
    await asyncio.sleep(0.05)
    assert cache.version == v
```

### 13.4 Massive (no network; `SimpleNamespace` snapshots)

`MagicMock` snapshots invent any attribute you read, which is how a wrong attribute name (`last_trade.timestamp`) passed the existing tests. Use `SimpleNamespace`, which raises/returns nothing for attributes you didn't define:

```python
import asyncio
import time
from types import SimpleNamespace as NS

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource, _to_unix_seconds, parse_snapshot


def test_timestamp_units():
    assert _to_unix_seconds(1_700_000_000_123_456_789) == 1_700_000_000.1234567  # ns
    assert _to_unix_seconds(1_700_000_000_123) == 1_700_000_000.123  # ms
    assert _to_unix_seconds(1_700_000_000) == 1_700_000_000  # s
    assert _to_unix_seconds(None) is None and _to_unix_seconds(0) is None


def test_parse_full_snapshot():
    snap = NS(ticker="aapl",
              last_trade=NS(price=190.5, sip_timestamp=1_700_000_000_000_000_000),
              min=None, day=NS(close=190.0), prev_day=NS(close=188.0),
              todays_change=2.5, updated=None)
    p = parse_snapshot(snap)
    assert (p.ticker, p.price, p.timestamp, p.reference_price) == ("AAPL", 190.5, 1.7e9, 188.0)


def test_parse_fallbacks():
    p = parse_snapshot(NS(ticker="X", last_trade=None, day=NS(close=0), prev_day=NS(close=50.0)))
    assert p.price == 50.0 and p.timestamp is None
    assert parse_snapshot(NS(ticker="X", last_trade=NS(price=10.0), todays_change=1.0)).reference_price == 9.0
    assert parse_snapshot(NS(ticker="X", last_trade=None)) is None


async def test_removed_mid_request_is_not_resurrected():
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60)
    src._client, src._tickers = object(), ["AAPL", "MSFT"]

    def slow_fetch(tickers):
        time.sleep(0.1)  # runs in a worker thread
        return [NS(ticker=t, last_trade=NS(price=100.0)) for t in tickers]

    src._fetch_snapshots = slow_fetch
    poll = asyncio.create_task(src._poll_once())
    await asyncio.sleep(0.02)
    await src.remove_ticker("MSFT")
    await poll
    assert "AAPL" in cache and "MSFT" not in cache


async def test_failures_back_off_and_keep_last_prices():
    cache = PriceCache()
    cache.update("AAPL", 100.0)
    src = MassiveDataSource("k", cache, poll_interval=60)
    src._client, src._tickers = object(), ["AAPL"]

    def boom(_):
        raise RuntimeError("429")

    src._fetch_snapshots = boom
    await src._poll_once()
    await src._poll_once()
    assert src._next_delay() == 120.0 and cache.get_price("AAPL") == 100.0

    src._fetch_snapshots = lambda _: [NS(ticker="AAPL", last_trade=NS(price=101.0))]
    await src._poll_once()
    assert src._next_delay() == 60 and cache.get_price("AAPL") == 101.0
```

### 13.5 SSE generator (no server needed)

Test the generator directly with a fake request. `httpx.ASGITransport` buffers whole responses, so it hangs on an infinite stream.

```python
from app.market.cache import PriceCache
from app.market.stream import create_stream_router, generate_price_events


class FakeRequest:
    client = None

    def __init__(self, polls: int):
        self._left = polls

    async def is_disconnected(self) -> bool:
        self._left -= 1
        return self._left < 0


async def test_stream_frames_and_heartbeat():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    frames = [f async for f in generate_price_events(cache, FakeRequest(8), interval=0.01, heartbeat=0.03)]
    assert frames[0] == "retry: 1000\n\n"
    assert frames[1].startswith('id: 1\ndata: {"AAPL"')
    assert ": heartbeat\n\n" in frames
    assert sum(f.startswith("id:") for f in frames) == 1  # unchanged cache → no resend


def test_router_factory_is_reentrant():
    a, b = create_stream_router(PriceCache()), create_stream_router(PriceCache())
    assert a is not b and len(a.routes) == 1
```

### 13.6 Interface conformance & tracking

```python
import pytest

from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource
from app.market.tracking import sync_tracked_tickers


@pytest.mark.parametrize("cls", [SimulatorDataSource, MassiveDataSource])
def test_implements_interface(cls):
    assert issubclass(cls, MarketDataSource) and not cls.__abstractmethods__


async def test_sync_adds_and_removes():
    src = SimulatorDataSource(PriceCache())
    await src.start(["AAPL", "GOOGL"])
    await sync_tracked_tickers(src, ["aapl", "TSLA"])
    assert sorted(src.get_tickers()) == ["AAPL", "TSLA"]
    await src.stop()
```

---

## 14. Error Handling & Edge Cases

| Situation | Behavior |
|---|---|
| Empty watchlist at startup | Sources start with no tickers. Simulator steps return `{}`, Massive skips its request. SSE sends `{}`. Adding a ticker later starts it normally. |
| Trade on a ticker with no cached price | Route calls `add_ticker` first. Simulator prices it immediately; Massive returns 400 *"No price available yet"* until the next poll. |
| Unknown/invalid symbol, simulator | Gets a random $50–300 seed and default params. (Accepting anything is fine for a sim.) |
| Unknown/invalid symbol, Massive | Never appears in snapshot results, so it stays priceless (logged at debug). The watchlist row shows "—". Optional: the watchlist POST may validate with `client.get_snapshot_ticker(...)`, at the cost of one extra request. |
| Invalid `MASSIVE_API_KEY` (401/403) | First poll fails, then the poller backs off to 120 s and keeps retrying. Cache stays empty and SSE sends `{}`. Fix the key and restart. Logged at ERROR for the first 3 failures. |
| 429 rate limit | Backoff doubles the delay per failure (cap 120 s); resets on first success. Last-known prices keep streaming. |
| Massive slow at startup | `start()` awaits one poll; the `massive` client's own timeouts/retries bound it. App startup waits a few seconds at worst. |
| Market closed | `last_trade.price` is the last (possibly after-hours) trade. `direction` stays `flat` between polls with no new trades, which is correct. |
| Ticker removed during an in-flight Massive request | Its results are discarded (§9.3). |
| Simulator step raises | Logged with traceback; loop continues next tick. |
| SSE client disconnects | `is_disconnected()` ends the generator within ≤ 0.5 s; `finally` logs it. Server shutdown cancels it cleanly. |
| Removing a held ticker from the watchlist | `sync_tracked_tickers` keeps it tracked; it disappears from the watchlist panel but stays in the stream for portfolio valuation. |

---

## 15. Configuration Reference

| Setting | Where | Default | Notes |
|---|---|---|---|
| `MASSIVE_API_KEY` | env | empty | Non-empty (after trimming) → Massive |
| `MASSIVE_POLL_INTERVAL` | env | `15` | Seconds; floor 1. **Δ** |
| `update_interval` | `SimulatorDataSource(...)` | `0.5` s | Simulator tick |
| `event_probability` | `SimulatorDataSource` / `GBMSimulator` | `0.001` | Per ticker per tick |
| `seed` | `SimulatorDataSource` / `GBMSimulator` | `None` | Deterministic runs for tests **Δ** |
| `dt` | `GBMSimulator` | `0.5 / 5,896,800` | Tick as fraction of trading year |
| `MAX_BACKOFF_SECONDS` | `massive_client.py` | `120` | Upper bound on failure backoff **Δ** |
| `PUSH_INTERVAL` | `stream.py` | `0.5` s | SSE cache-check cadence |
| `HEARTBEAT_INTERVAL` | `stream.py` | `15` s | Idle keep-alive **Δ** |
| `RETRY_MS` | `stream.py` | `1000` | EventSource reconnect delay |

---

## 16. Change List vs. Current Implementation

A checklist for whoever updates `backend/app/market/` to match this design. Each item is small and independent.

**Correctness**

- [ ] **Massive timestamp/attribute parsing**: replace `snap.last_trade.timestamp / 1000` with `parse_snapshot()` (§9.2–9.3). First confirm the attribute names against the installed `massive` package.
- [ ] **Massive mid-request removal**: drop results for tickers no longer tracked; pass a copy of the ticker list to the worker thread.
- [ ] **Cache `remove()` bumps version** so SSE reflects removals promptly.
- [ ] **Cache `timestamp is None`** check instead of `or`.
- [ ] **SSE router created inside the factory** (no module-level `router`).

**Features required by PLAN.md**

- [ ] `reference_price` / `day_change` / `day_change_percent` on `PriceUpdate`, set by both sources (daily change %).
- [ ] `tracking.py` with `sync_tracked_tickers()`; track watchlist ∪ positions.
- [ ] `normalize_ticker()` used by both sources.

**Robustness & ergonomics**

- [ ] Massive failure backoff; `MASSIVE_POLL_INTERVAL` env var (+ `.env.example`).
- [ ] SSE heartbeat, `id:` field, `PriceCache.snapshot()`.
- [ ] Simulator `seed` parameter with private RNGs.

**Tests**

- [ ] Replace `MagicMock` snapshots with `SimpleNamespace` in `test_massive.py`.
- [ ] Add `test_stream.py`, `test_tracking.py`, the correlation/volatility checks, and the 10-ticker and 70-ticker Cholesky tests (§13).

Public names and signatures used by downstream code are unchanged: `PriceCache.update(ticker, price, timestamp=None)` still works, and the new `reference_price` argument is optional. So none of these changes touch code outside `app/market/`.
