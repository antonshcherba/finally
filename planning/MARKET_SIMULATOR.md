# Market Simulator

How FinAlly generates realistic-looking live prices when no (usable) `MASSIVE_API_KEY` is configured. This is the default data source, so it must feel alive, be deterministic enough to test, and need no network.

Related: `MARKET_INTERFACE.md` (the interface it implements), `PLAN.md` §6 (requirements). Code: `backend/app/market/simulator.py`, `seed_prices.py`.

---

## 1. Requirements (from PLAN §6)

- Geometric Brownian motion (GBM) with configurable drift and volatility **per ticker**
- ~500 ms update interval, in-process background task, no external dependencies
- **Correlated** moves across tickers (tech together)
- Occasional random **events**: sudden 2–5% moves
- Realistic seed prices (AAPL ≈ $190 …)
- Supports a **fixed known set**: the 10 default tickers. Anything else is rejected (`400 unknown_ticker`). Adding tickers later = adding entries to `seed_prices.py`
- Provides `baseline_price` (= seed price) for "daily change %"

## 2. Code structure

```
backend/app/market/
  seed_prices.py   data only: SEED_PRICES, TICKER_PARAMS, CORRELATION_GROUPS, *_CORR constants
  simulator.py     GBMSimulator        pure math, synchronous, no asyncio, no cache
                   SimulatorDataSource asyncio loop adapting GBMSimulator to MarketDataSource + PriceCache
```

The split is deliberate:

| Class | Knows about | Does not know about |
|---|---|---|
| `GBMSimulator` | prices, params, correlation, `step()` | asyncio, `PriceCache`, SSE, time |
| `SimulatorDataSource` | the 500 ms loop, cache writes, ticker add/remove lifecycle | the math |

so the math can be unit-tested synchronously and fast (call `step()` 10,000 times), while the data source only needs a light async test.

### `GBMSimulator` API

```python
class GBMSimulator:
    def __init__(self, tickers: list[str], dt: float = DEFAULT_DT, event_probability: float = 0.001): ...
    def step(self) -> dict[str, float]      # advance all tickers once -> {ticker: price rounded to cents}
    def add_ticker(self, ticker: str) -> None
    def remove_ticker(self, ticker: str) -> None
    def get_price(self, ticker: str) -> float | None
    def get_tickers(self) -> list[str]
    def get_baseline(self, ticker: str) -> float | None      # NEW: SEED_PRICES[ticker]
    @staticmethod
    def is_known(ticker: str) -> bool                        # NEW: ticker in SEED_PRICES
```

### `SimulatorDataSource` (implements `MarketDataSource`)

```python
class SimulatorDataSource(MarketDataSource):
    async def start(self, tickers)        # build GBMSimulator, seed cache immediately, spawn loop
    async def stop(self)                  # cancel + await the task; idempotent
    async def add_ticker(self, t)         # sim.add_ticker + seed the cache so it is priced at once
    async def remove_ticker(self, t)      # sim.remove_ticker + cache.remove
    async def validate_ticker(self, t)    # NEW: GBMSimulator.is_known(t)
    def get_tickers(self)
    async def _run_loop(self)             # while True: step → cache.update for each → sleep(interval)
```

Seeding the cache in `start()`/`add_ticker()` means the SSE stream and the trade endpoint have a price *before* the first tick, avoiding a spurious `price_unavailable` right after startup or a watchlist add.

## 3. The math

Each tick, every ticker evolves by the exact GBM solution over one step:

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z )
```

- `μ` annualised drift, `σ` annualised volatility (from `TICKER_PARAMS`)
- `Z` standard normal, **correlated across tickers** (§4)
- `dt = 0.5 s / (252 days · 6.5 h · 3600 s) ≈ 8.48e-8`: one tick expressed as a fraction of a *trading* year

Using the exponential form (rather than `S += S·(μdt + σ√dt·Z)`) guarantees prices stay positive and makes the `−σ²/2` Itô correction explicit; with such a small `dt` both are numerically near-identical, but exp can never go negative.

### What that looks like on screen (computed from the shipped parameters)

| Ticker | σ | Typical 500 ms move | 1-hour σ of return |
|---|---|---|---|
| AAPL | 0.22 | ≈ $0.012 (0.006%) | ≈ 0.54% |
| NVDA | 0.40 | ≈ $0.093 (0.012%) | ≈ 0.98% |
| TSLA | 0.50 | ≈ $0.036 (0.015%) | ≈ 1.2% |
| V | 0.17 | ≈ $0.014 (0.005%) | ≈ 0.4% |

Consequences worth knowing:
- Ordinary ticks are **about one cent**, and prices are rounded to cents, so low-priced tickers often print "flat" or ±1¢. That is realistic, and the green/red flash still triggers on any cent change.
- Without shocks the day would look sleepy. **Events carry the visual drama** (§5).
- Time runs on *ticks*, not wall-clock: one simulated "trading hour" is 7,200 ticks = 1 real hour. A 6.5 h session therefore has ≈ 1.4% σ for AAPL. If sleeps drift under load, simulated time slows rather than jumps.

## 4. Correlation

Independent draws are mapped to correlated draws with a Cholesky factor of the correlation matrix `C = L·Lᵀ`:

```
z_independent ~ N(0, I)           (np.random.standard_normal(n))
z_correlated  = L @ z_independent  (each ticker's Z, correlation = C)
```

Pairwise correlation rules (`_pairwise_correlation`, constants in `seed_prices.py`):

| Pair | ρ |
|---|---|
| both in `tech` (AAPL GOOGL MSFT AMZN META NVDA NFLX) | 0.6 |
| both in `finance` (JPM V) | 0.5 |
| TSLA with anything | 0.3 (checked first, so TSLA is "its own thing" despite being a tech name) |
| cross-sector | 0.3 |

`L` is rebuilt whenever a ticker is added or removed (O(n²), n ≤ 10, negligible). Verified: for the 10 default tickers the matrix is positive definite (smallest eigenvalue 0.40), and any uniform-0.3 extension stays positive definite, so `np.linalg.cholesky` cannot fail as the known set grows with the same group rules. If a future group uses a high intra-group ρ with many members, re-check positive definiteness; a failure raises `LinAlgError` inside `add_ticker`.

Correlation only shapes the continuous GBM part. **Shocks are independent per ticker** (§5), which is intentional: a market-wide crash would be a different feature.

## 5. Random events

After each ticker's GBM update, with probability `event_probability` (default `0.001` per ticker per tick):

```python
if random.random() < self._event_prob:
    magnitude = random.uniform(0.02, 0.05)        # 2–5%
    sign = random.choice([-1, 1])
    self._prices[ticker] *= 1 + magnitude * sign
```

Frequency with 10 tickers at 2 ticks/s: 10 × 2 × 0.001 = 0.02 events/s, i.e. **one every ~50 s across the watchlist (~1.2/min), and about once per ~8 min for any single ticker**. A 2–5% jump is 1.5–4× AAPL's whole-session σ, so it is deliberately dramatic. The jump is permanent (no mean reversion).

Tuning: `event_probability` is a constructor argument threaded through `SimulatorDataSource`, so tests can set `0.0` (pure GBM) or `1.0` (every tick).

## 6. Seed data and baselines

`seed_prices.py` is data only:

```python
SEED_PRICES = {"AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00, "TSLA": 250.00,
               "NVDA": 800.00, "META": 500.00, "JPM": 195.00, "V": 280.00, "NFLX": 600.00}
TICKER_PARAMS = {"AAPL": {"sigma": 0.22, "mu": 0.05}, ...}   # TSLA 0.50/0.03, NVDA 0.40/0.08, JPM 0.18/0.04, V 0.17/0.04
```

- Seeds are approximations, not live quotes. Update them occasionally for plausibility; nothing depends on exact values.
- **`baseline_price` = `SEED_PRICES[ticker]`**, constant for the process lifetime, so daily change % = `(price − seed)/seed`. Because the walk has no mean reversion, change % can drift far from zero in a very long session; that is acceptable for a demo and avoids introducing a fake "daily reset" clock.
- Restarting the server **restarts every price at its seed** (prices are not persisted). Positions in the DB keep their `avg_cost`, so P&L after a restart is relative to the old fills. Persisting last prices is possible but out of scope.

### Adding a ticker to the supported set

1. Add `"XYZ": price` to `SEED_PRICES`.
2. Add `"XYZ": {"sigma": ..., "mu": ...}` to `TICKER_PARAMS`.
3. Add it to a group in `CORRELATION_GROUPS` if it belongs to one (otherwise cross-sector 0.3 applies).
4. No other code changes: validation (`is_known`), the Cholesky rebuild and the data source all read these tables.

## 7. Changes required to the existing implementation

The shipped simulator predates PLAN's decisions:

| Today | Target |
|---|---|
| `_add_ticker_internal` accepts any symbol: `SEED_PRICES.get(ticker, random.uniform(50, 300))` and `DEFAULT_PARAMS` | Unknown symbols rejected. Remove `DEFAULT_PARAMS` and the random seed; `add_ticker` raises `UnknownTicker`. Routes call `validate_ticker` first, so this is defence in depth |
| No baseline | `get_baseline()`; `SimulatorDataSource` passes `baseline_price=` to `PriceCache.update` |
| `random` / `np.random` module-level global state | Optional: accept `seed: int | None` and hold `random.Random(seed)` / `np.random.default_rng(seed)` so tests can be exactly reproducible without patching globals |
| Loop swallows all exceptions per tick | Keep (a bad tick must not kill the feed) but `logger.exception` already records it |

Threading: `GBMSimulator` is touched only from the event-loop thread (loop + add/remove coroutines), so it needs no lock; only `PriceCache` is lock-protected because other threads (`asyncio.to_thread` workers, request handlers) may read it.

## 8. Data flow per tick

```
sleep(0.5s) ─► sim.step()
                 ├─ z = L @ N(0,I)                       correlated normals
                 ├─ for each ticker:
                 │     S *= exp((μ−σ²/2)dt + σ√dt·z_i)
                 │     maybe S *= 1 ± U(2%,5%)           event
                 │     out[ticker] = round(S, 2)
                 └─ return out
             ─► for each (ticker, price): cache.update(ticker, price, baseline_price=seed)
             ─► cache.version += 1   ─►  SSE loop sees a new version and emits events
```

The internal state `self._prices` is kept **unrounded**; only the emitted price is rounded, so sub-cent drift accumulates instead of being truncated away on every step.

## 9. Testing

Backend unit tests (`backend/tests/market/`), all synchronous except the data-source ones:

1. **Positivity & finiteness**: 10,000 `step()` calls, every price > 0 and finite.
2. **GBM statistics** (with `event_probability=0`, fixed seed): over many steps, mean log-return ≈ `(μ−σ²/2)·dt` and stdev ≈ `σ√dt` within tolerance. Use a larger `dt` in the test to get a measurable signal.
3. **Correlation**: simulate N steps, the empirical correlation of AAPL/MSFT log-returns ≈ 0.6 (±0.05), AAPL/JPM ≈ 0.3, TSLA/anything ≈ 0.3.
4. **Events**: `event_probability=1.0` → every step moves at least ~2% (beyond GBM noise); `0.0` → no step exceeds a few σ.
5. **Known-set enforcement**: `add_ticker("ZZZZ")` raises; `validate_ticker("AAPL")` is true, `"ZZZZ"` false; `get_baseline("AAPL") == 190.0`.
6. **Add/remove**: Cholesky rebuilt (shape matches), removing then re-adding resets to seed, no-ops on duplicates.
7. **Data source** (async): after `start()` the cache already holds all tickers; after `~0.6 s` versions advanced; `add_ticker` is priced immediately; `remove_ticker` evicts from the cache; `stop()` is idempotent.

E2E tests (Playwright) rely on the simulator being the default: they assert that prices *change* over a few seconds and the SSE reconnects, never on specific values.

## 10. Known limitations (accepted)

- No market hours, halts, gaps or earnings: it trades 24/7 at constant volatility.
- No mean reversion; long runs can wander far from seeds (baseline stays at seed).
- Shocks are idiosyncratic only; no market-wide events.
- Unknown-symbol rejection means the simulator cannot price tickers like IBM until they are added to `seed_prices.py`; with a paid Massive key any listed symbol works (`MARKET_INTERFACE.md` §5.2).
