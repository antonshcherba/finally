# Backend — Developer Guide

## Project Setup

```bash
cd backend
uv sync --extra dev   # Install all dependencies including test/lint tools
```

## Market Data API

The market data subsystem lives in `app/market/`. Use these imports:

```python
from app.market import (
    PriceCache, PriceUpdate, MarketDataSource, start_market_data,
    create_stream_router, require_known, require_price,
)
```

### Core Types

- **`PriceUpdate`** — Immutable dataclass: `ticker`, `price`, `previous_price`, `baseline_price`, `timestamp` (Unix seconds), plus properties `change`, `change_percent`, `direction` ("up"/"down"/"flat", vs. the previous update), `daily_change_percent` (vs. `baseline_price`), and `to_sse()` — the exact PLAN §6 payload (ISO timestamp).
  `baseline_price` is the simulator's seed price, or Massive's previous close.

- **`PriceCache`** — Thread-safe in-memory store. Key methods:
  - `update(ticker, price, timestamp=None, baseline_price=None) -> PriceUpdate` (baseline is sticky; first price is the baseline if none given)
  - `get(ticker)`, `get_price(ticker)`, `get_all()`, `snapshot() -> (version, prices)`
  - `remove(ticker)`
  - `version` — monotonic counter, bumped on every update and removal

- **`MarketDataSource`** — Abstract interface implemented by `SimulatorDataSource` and `MassiveDataSource`. `start(tickers)`, `stop()`, `add_ticker()`, `remove_ticker()`, `get_tickers()`, `validate_ticker()`, plus the concrete `set_tickers(desired)` that reconciles the tracked set (pass watchlist ∪ open positions). `source.mode` is `"simulator"` or `"massive"`.

- **`start_market_data(cache, tickers)`** — Lifespan entry point. Uses Massive if `MASSIVE_API_KEY` is set, falling back to the simulator if the key/plan is rejected (snapshots need Starter+). `create_market_data_source(cache)` returns an unstarted source (for tests).

### Ticker handling (API boundary)

- `normalize_ticker(raw)` — trim + uppercase; raises `InvalidTicker` (-> 400 `invalid_ticker`) unless 1–5 letters.
- `await require_known(source, raw)` — also raises `UnknownTicker` (-> 400 `unknown_ticker`). The simulator knows only the tickers in `seed_prices.py`; Massive validates by asking the API. A transient failure raises `PriceUnavailable` (-> 503), never `UnknownTicker`.
- `require_price(cache, ticker)` — raises `PriceUnavailable` when there is no cached price yet.
- Trade flow for a ticker that may not be tracked: `require_known` -> `source.add_ticker` -> `require_price`.

### SSE Streaming

```python
router = create_stream_router(price_cache)  # fresh APIRouter per call
# GET /api/stream/prices (text/event-stream)
```

Every ~500 ms the stream sends one event per tracked ticker (no diffing); an idle stream sends a `: heartbeat` comment. With Massive the values repeat between polls, so clients should flash only when `price` changes.

### Seed Data

Supported tickers: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX. Seed prices (= baselines) and per-ticker volatility/drift params are in `app/market/seed_prices.py`. To support another ticker, add it to `SEED_PRICES` and `TICKER_PARAMS`.

### Environment

`MASSIVE_API_KEY` (optional), `MASSIVE_POLL_INTERVAL` (seconds, default 15, floor 1).

## Running Tests

```bash
uv run --extra dev pytest -v              # All tests
uv run --extra dev pytest --cov=app       # With coverage
uv run --extra dev ruff check app/ tests/ # Lint
```

## Demo

```bash
uv run market_data_demo.py   # Live terminal dashboard with simulated prices
```
