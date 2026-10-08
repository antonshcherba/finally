# Market Data Interface

The unified Python API for stock prices in FinAlly. Uses the Massive API when `MASSIVE_API_KEY` is set and usable, otherwise the built-in simulator. All downstream code (SSE, portfolio, trades, watchlist) sees one interface.

Companion docs: `MASSIVE_API.md` (provider research), `MARKET_SIMULATOR.md` (simulator design), `PLAN.md` §6 (requirements). This doc describes the **target** design and the delta from the code in `backend/app/market/` as it exists today (§2).

---

## 1. Design in one picture

```
 MASSIVE_API_KEY?          ┌────────────────────────────┐
  yes ─────────────────►   │ MassiveDataSource          │──┐ poll snapshot every N s (1 call)
  no / unusable ───────►   │ SimulatorDataSource        │──┤ GBM step every 500 ms
                           └────────────────────────────┘  │ writes
                                      ▲ add/remove/set_tickers
                                      │                    ▼
   watchlist ∪ positions ─────────────┘            ┌──────────────┐
                                                   │  PriceCache  │  latest PriceUpdate per ticker
                                                   └──────┬───────┘  (+ baseline_price, version counter)
                                     reads ┌──────────────┼───────────────┐
                                           ▼              ▼               ▼
                                   SSE /api/stream   trade execution   portfolio / watchlist GET
```

Principles (unchanged from the completed module, they are good):
- **Push into a cache, never pull from the source.** Sources write on their own schedule; consumers read the cache. Nothing downstream knows which source is active.
- **`PriceUpdate` is the only type leaving this layer.**
- **One writer at a time** (the active source); readers are many, hence a lock in the cache.

## 2. Current code vs. target

What `backend/app/market/` does today and what this design changes:

| # | Area | Today | Target | Why |
|---|---|---|---|---|
| 1 | **Massive price parsing** | `snap.last_trade.timestamp / 1000` | `sip_timestamp` via `to_epoch_seconds()`; price fallback chain | **Bug:** `LastTrade` has no `.timestamp`; the `AttributeError` is caught per snapshot, so *every* snapshot is skipped and the cache stays empty. Tests pass only because they build snapshots with `MagicMock` |
| 2 | Baseline price | absent | `PriceUpdate.baseline_price` (simulator: seed price; Massive: `prev_day.close`) | PLAN §6 |
| 3 | Unknown tickers | simulator accepts anything (random seed price) | `validate_ticker()`; unknown → `400 unknown_ticker` | PLAN §6 / §8 |
| 4 | Tracked set | `add_ticker`/`remove_ticker` only | plus concrete `set_tickers(desired)` for watchlist ∪ positions | PLAN §6 |
| 5 | SSE shape | one event holding `{ticker: {...}}` for all tickers; float timestamp | one event per ticker; ISO timestamp; `baseline_price` included | PLAN §6 contract |
| 6 | Free-key behaviour | polls snapshots forever, logging errors | detect `NOT_AUTHORIZED` at start → fall back to simulator | Snapshots are not on the free plan (`MASSIVE_API.md` §2) |
| 7 | Imports | factory imports `massive` at module top | unchanged, `massive` is a core dependency | fine; note simulator-only users still need the wheel |

Everything else (ABC lifecycle, `PriceCache` locking, version counter, GBM, Cholesky) stays.

## 3. Data model

```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float
    previous_price: float          # prior cache value (prior poll / prior tick)
    baseline_price: float          # NEW: seed price (sim) or previous close (Massive)
    timestamp: float = field(default_factory=time.time)   # Unix seconds, always

    change, change_percent, direction      # as today (vs previous_price)

    @property
    def daily_change_percent(self) -> float:
        """(price - baseline) / baseline * 100: what the watchlist shows."""
        return round((self.price - self.baseline_price) / self.baseline_price * 100, 4) if self.baseline_price else 0.0

    def to_sse(self) -> dict:
        """Exactly the PLAN §6 payload."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "baseline_price": self.baseline_price,
            "timestamp": datetime.fromtimestamp(self.timestamp, UTC).isoformat(),
            "direction": self.direction,
        }
```

Internal timestamps stay float seconds (cheap, comparable); ISO conversion happens only at the API edge (`to_sse`, REST responses).

`PriceCache.update(ticker, price, timestamp=None, baseline_price=None)`: if `baseline_price` is `None` the previous baseline is kept; on a ticker's first update with no baseline, baseline = price.

## 4. Source interface

```python
class MarketDataSource(ABC):
    # --- lifecycle (as today) ---
    async def start(self, tickers: list[str]) -> None: ...
    async def stop(self) -> None: ...                      # idempotent

    # --- tracked set ---
    async def add_ticker(self, ticker: str) -> None: ...   # no-op if present
    async def remove_ticker(self, ticker: str) -> None: ...# no-op if absent; also evicts from cache
    def get_tickers(self) -> list[str]: ...

    # --- NEW ---
    @abstractmethod
    async def validate_ticker(self, ticker: str) -> bool:
        """True if this source can price `ticker`. Input is already normalised."""

    async def set_tickers(self, desired: Iterable[str]) -> None:
        """Make the tracked set equal `desired` (diff of add/remove). Concrete, shared."""
        want, have = set(desired), set(self.get_tickers())
        for t in sorted(want - have):
            await self.add_ticker(t)
        for t in sorted(have - want):
            await self.remove_ticker(t)
```

Ticker normalisation lives **outside** the sources, once, at the API boundary:

```python
# app/market/tickers.py
_TICKER_RE = re.compile(r"^[A-Z]{1,5}$")

class InvalidTicker(ValueError): ...      # -> 400 invalid_ticker
class UnknownTicker(ValueError): ...      # -> 400 unknown_ticker

def normalize_ticker(raw: str) -> str:
    t = raw.strip().upper()
    if not _TICKER_RE.match(t):
        raise InvalidTicker(raw)
    return t

async def require_known(source: MarketDataSource, raw: str) -> str:
    t = normalize_ticker(raw)
    if not await source.validate_ticker(t):
        raise UnknownTicker(t)
    return t
```

Both the watchlist-add route and the trade service (including the chat path, since it shares the trade service) call `require_known`. That is the single place that enforces PLAN §8's `invalid_ticker` / `unknown_ticker`.

### Who computes the tracked set

A small service function, called after every watchlist or position change and once at startup:

```python
async def sync_tracked(source: MarketDataSource, db) -> None:
    await source.set_tickers(db.watchlist_tickers() | db.position_tickers())
```

Removing a watchlist ticker that still has an open position therefore does not stop its pricing, as PLAN requires; selling the last share of an unwatched ticker drops it.

## 5. Implementations

### 5.1 Simulator (details in `MARKET_SIMULATOR.md`)

- `validate_ticker(t)` → `t in SEED_PRICES`. Cheap and synchronous.
- `baseline_price` = seed price, constant for the process lifetime.
- `add_ticker` of an unknown symbol raises `UnknownTicker` (defence in depth; routes validate first).

### 5.2 Massive

Behaviour of a poll cycle (one HTTP call regardless of watchlist size):

```python
class MassiveDataSource(MarketDataSource):
    def __init__(self, api_key, price_cache, poll_interval: float = 15.0): ...

    async def start(self, tickers):
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(tickers)
        await self._poll_once(raise_auth=True)          # fail fast: MarketDataAuthError
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")

    async def _poll_once(self, raise_auth: bool = False) -> None:
        if not self._tickers:
            return
        try:
            snaps = await asyncio.to_thread(self._fetch)   # client is synchronous
        except BadResponse as e:
            if _is_auth_error(e):
                if raise_auth:
                    raise MarketDataAuthError(str(e)) from e
                logger.error("Massive auth/plan error: %s", e)   # key revoked mid-run
            else:
                logger.warning("Massive poll failed: %s", e)
            return                                          # keep last prices, retry next cycle
        except Exception:
            logger.exception("Massive poll failed")
            return
        wanted = set(self._tickers)
        for s in snaps:
            price = snapshot_price(s)
            if price is None or s.ticker not in wanted:
                continue
            self._cache.update(
                s.ticker, price,
                timestamp=snapshot_timestamp(s),
                baseline_price=s.prev_day.close if s.prev_day else None,
            )

    def _fetch(self):
        return self._client.get_snapshot_all(SnapshotMarketType.STOCKS, tickers=self._tickers)
```

Helpers (`snapshot_price`, `snapshot_timestamp`, `to_epoch_seconds`, `_is_auth_error`) are specified in `MASSIVE_API.md` §4/§6/§7. This logic was exercised against real `massive` model objects (price fallback to `prev_day.close`, ns→s timestamp, baseline capture, `NOT_AUTHORIZED` → `MarketDataAuthError`).

`validate_ticker(t)`:
1. `t in self._tickers and t in cache` → `True` (already priced).
2. Else one `get_snapshot_ticker(SnapshotMarketType.STOCKS, t)` call (or a filtered `get_snapshot_all`) in a thread; `True` iff the result has a price per `snapshot_price`. Memoise positive results in a set (negative results are not cached, a symbol can start trading).
3. Error handling follows the poll rules: transient error → raise `PriceUnavailable` (maps to `503 price_unavailable`), not `False`, so a network blip is never reported as "unknown ticker".

`add_ticker` appends and triggers an immediate out-of-cycle poll (`asyncio.create_task(self._poll_once())`) so a newly added ticker is priced within a second, not after up to 15 s. Without this, a user adding a ticker would wait a full interval, and `POST /api/portfolio/trade` for it would get `price_unavailable`.

Poll interval: `MASSIVE_POLL_INTERVAL` env var (seconds), default `15` (Starter/Developer); set `2`–`5` on Advanced. Documented in `.env.example`.

## 6. Factory and startup

```python
async def start_market_data(cache: PriceCache, tickers: list[str]) -> MarketDataSource:
    """Pick, start and return a source. Never raises for a bad Massive key."""
    key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if key:
        source = MassiveDataSource(api_key=key, price_cache=cache, poll_interval=_poll_interval())
        try:
            await source.start(tickers)
            logger.info("Market data: Massive (%d tickers)", len(tickers))
            return source
        except MarketDataAuthError as e:
            logger.error("MASSIVE_API_KEY rejected or plan lacks snapshots (%s); using simulator", e)
            await source.stop()
    source = SimulatorDataSource(price_cache=cache)
    await source.start(tickers)
    return source
```

`create_market_data_source(cache)` (today's unstarted factory) remains for tests; `start_market_data` is what the FastAPI lifespan calls. The lifespan stores the chosen source on `app.state.market_source` and exposes `app.state.market_mode` (`"massive"` | `"simulator"`) for `GET /api/health` so the UI/E2E can show which one is live.

## 7. Decision: free-tier Massive keys

A free key cannot use snapshots (`MASSIVE_API.md` §2), so "key present" does not imply "live data available".

| Option | Behaviour | Cost |
|---|---|---|
| **A. Fall back to simulator (recommended, implemented above)** | Free/invalid key → loud log, simulator runs, `market_mode="simulator"` | none; app always works; the user gets simulated rather than real prices |
| B. EOD mode | Fetch yesterday's closes with one `get_grouped_daily_aggs` call at start, serve them as static prices | extra source class; prices never move, which defeats the terminal demo; unknown-ticker validation needs a per-ticker call (5/min) |

Recommendation: A. B is straightforward to add later behind the same interface (a third `MarketDataSource` whose poll loop is a no-op after the first fetch). **PLAN.md §6 currently claims free-tier 15 s snapshot polling works; it should be changed to say snapshots need Starter+.** Limitation of A: if the DB holds symbols the simulator does not know (e.g. data created under a paid key), `set_tickers` skips them with a warning and positions in them are shown at cost.

## 8. Errors → API

| Raised in market layer | HTTP | Code |
|---|---|---|
| `InvalidTicker` | 400 | `invalid_ticker` |
| `UnknownTicker` (`validate_ticker` false) | 400 | `unknown_ticker` |
| `PriceUnavailable` (no cache entry / transient validate failure) | 503 | `price_unavailable` |
| `MarketDataAuthError` | never surfaces; handled at startup (§6) | n/a |

`PriceUnavailable` is raised by a helper consumers use instead of touching the cache directly:

```python
def require_price(cache: PriceCache, ticker: str) -> PriceUpdate:
    update = cache.get(ticker)
    if update is None:
        raise PriceUnavailable(ticker)
    return update
```

## 9. How consumers use it

```python
# lifespan
cache = PriceCache()
tickers = db.watchlist_tickers() | db.position_tickers()
source = await start_market_data(cache, sorted(tickers))
app.include_router(create_stream_router(cache))

# POST /api/watchlist
t = await require_known(source, body.ticker)
db.add_watchlist(t); await sync_tracked(source, db)

# trade service (shared by /api/portfolio/trade and chat)
t = await require_known(source, req.ticker)
price = require_price(cache, t).price          # fill at current price

# GET /api/watchlist, /api/portfolio
u = cache.get(t); u.price, u.baseline_price, u.daily_change_percent

# shutdown
await source.stop()
```

SSE: every ~500 ms emit one `data:` event per tracked ticker via `to_sse()` (PLAN: "all tracked tickers each tick, no diffing"). With Massive the cache changes only each poll, so repeated identical prices are normal: the client flashes only when `price` differs from its last value.

## 10. File layout

```
backend/app/market/
  models.py          PriceUpdate (+baseline_price, to_sse)
  cache.py           PriceCache (+baseline handling)
  interface.py       MarketDataSource (+validate_ticker, set_tickers)
  tickers.py         NEW normalize_ticker, require_known, InvalidTicker/UnknownTicker
  errors.py          NEW PriceUnavailable, MarketDataAuthError
  seed_prices.py     SEED_PRICES, TICKER_PARAMS, correlation constants
  simulator.py       GBMSimulator + SimulatorDataSource
  massive_client.py  MassiveDataSource + snapshot_* helpers
  factory.py         create_market_data_source (tests), start_market_data (app)
  stream.py          SSE router
```

## 11. Testing requirements

1. **Never `MagicMock` the Massive models.** Build `TickerSnapshot.from_dict({...raw JSON...})` so attribute names are checked by the real client. The current suite hid bug #1 by mocking a nonexistent `last_trade.timestamp`.
2. Massive cases: normal snapshot; snapshot with no `lastTrade` (falls back to `prev_day.close`); `None` `prev_day`; ticker missing from response; ns timestamp conversion; `BadResponse('{"status":"NOT_AUTHORIZED"}')` at start → `MarketDataAuthError` → `start_market_data` returns a `SimulatorDataSource`; transient `BadResponse` mid-run keeps last prices.
3. Simulator cases: unknown ticker rejected; baseline equals seed; `set_tickers` diff.
4. `set_tickers` / `sync_tracked`: position-only ticker stays tracked after watchlist removal.
5. `to_sse()` matches the PLAN §6 key set exactly.
6. A gated live smoke test (`@pytest.mark.skipif(not os.environ.get("MASSIVE_API_KEY"))`) that fetches one snapshot and asserts a positive price and `prev_day.close`. This closes the "unverified live" items in `MASSIVE_API.md` §9.

## 12. Migration checklist

1. Fix `massive_client.py` price/timestamp extraction (bug #1) and rewrite its tests with real models.
2. Add `baseline_price` to `PriceUpdate`, `PriceCache.update`, both sources, and SSE.
3. Add `validate_ticker`, `set_tickers`, `tickers.py`, `errors.py`.
4. Reject unknown tickers in the simulator.
5. Add `start_market_data` with auth fallback; wire into lifespan; report `market_mode` in `/api/health`.
6. Change SSE to per-ticker events with ISO timestamps.
7. Update PLAN.md §6 (free-tier claim) and `.env.example` (`MASSIVE_POLL_INTERVAL`).
