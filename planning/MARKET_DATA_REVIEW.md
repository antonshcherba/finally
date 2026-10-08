# Market Data Backend — Code Review

Scope: `backend/app/market/` (13 modules incl. `errors.py`, `tickers.py`), `backend/tests/market/`, checked against `PLAN.md` §6/§8/§13 and the other planning docs (`MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md`, `MASSIVE_API.md`, `MARKET_DATA_DESIGN.md`, `REVIEW.md`).

## Verdict

**Ready to build on, with two small fixes recommended first (M1, M2).** The module now matches the PLAN contract on every point `REVIEW.md` flagged as High: SSE payload, baseline price, ticker validation, tracked-ticker reconcile, and the Massive field-name bug. No Critical or High issues remain. The findings below are Medium and Low. All were reproduced by running code, not just by reading it.

## Test results

| Check | Result |
|---|---|
| `pytest` (196 tests) | **196 passed**, ~2 s; run 4 times in a row with no flakes |
| Coverage | **99%** (558 statements, 2 missed: `massive_client.py:278`, the `_fetch_snapshots` body; `stream.py:41`, the route handler body) |
| `ruff check app tests` | clean |
| `market_data_demo.py` | runs until killed, no tracebacks |
| SSE over real HTTP | **not verified**: the sandbox blocks binding a local port. The generator and router are tested directly (see L3) |
| Live Massive API | **not verified**: no key available |

Environment note: the tests ran on Python 3.13.9 although `pyproject.toml` says `>=3.12`.

## What was checked against the plan

| Requirement | Status |
|---|---|
| Two sources behind one interface | Pass. Conformance test checks the exact abstract method set |
| `MASSIVE_API_KEY` empty → simulator | Pass (blank and whitespace keys tested) |
| GBM, correlated, events, 500 ms | Pass. Statistics tests verify σ·√dt, correlations 0.6/0.5/0.3, positivity, 2–5% events |
| Unknown tickers rejected (§6, §8) | Pass in the simulator; Massive validates via the API |
| `baseline_price` on cache and SSE (§6) | Pass |
| SSE payload exactly per §6, ISO timestamp | Pass (key-set test) |
| Tracked set = watchlist ∪ positions | Pass at the interface level (`set_tickers`); the caller that computes the union does not exist yet |
| Free-tier Massive handled | Pass: auth failure at start falls back to the simulator |
| `PriceCache` thread safety, version counter | Pass; concurrent-writer test |
| Poll interval configurable | Pass (`MASSIVE_POLL_INTERVAL`) |

## Findings

### Medium

**M1. `MassiveDataSource.start()` crashes on a malformed ticker; the simulator skips it.** Reproduced: `start(["AAPL", "bad ticker"])` raises `InvalidTicker`, so the whole app fails to start if the database ever holds a bad row. `SimulatorDataSource.start()` logs and skips the same input, and `set_tickers` skips it too. `massive_client.py` `_track` → `normalize_ticker`.
*Fix:* catch `InvalidTicker` in `start()` and skip with a warning, as the simulator does. Add a test; the current suite only covers this path for the simulator.

**M2. A burst of `add_ticker` calls makes one API call per ticker.** Reproduced: `set_tickers` adding 4 tickers made 4 snapshot requests. Each `add_ticker` spawns its own out-of-cycle poll, and each poll fetches the whole tracked set. Startup reconcile, reset (re-adding 10 default tickers), and chat actions that touch several tickers all trigger this. It wastes quota on plans with rate limits and risks 429s, which then trigger backoff.
*Fix:* coalesce: if an extra poll is already pending or running, don't start another (a single "poll requested" flag), or have `set_tickers` add all tickers first and poll once.

**M3. `_is_auth_error` matches too broadly.** The marker `"api key"` makes `"Your API key has exceeded its rate limit"` count as an auth error. Reproduced: it returns `True`. At startup that permanently drops to the simulator on what is only a rate limit, with an error log claiming the key was rejected. Real Massive bodies are not confirmed (`MASSIVE_API.md` §9 item 3), so the risk is unknown rather than certain.
*Fix:* match on `status == "NOT_AUTHORIZED"` (parse the JSON body) plus 401/403 only; drop the generic `"api key"`/`"apikey"` substrings. Re-check once a real error body is captured.

**M4. Nothing computes the tracked set or routes through `require_known` yet.** The module offers `set_tickers`, `require_known` and `require_price`, but the code that unions watchlist and positions, validates before trading, and calls `add_ticker` does not exist. Until it does, `REVIEW.md` items H3/H4 (position-only tickers keep being priced; trading an untracked ticker) are solved only in principle. The risk is concentrated in one place: if any route calls `remove_ticker` directly, a held ticker loses its price.
*Action for the backend work:* make `set_tickers` the only way routes change the tracked set, and add an integration test for "remove from watchlist while holding a position".

### Low

**L1. No negative caching on `validate_ticker`.** Reproduced: three validations of the same unknown symbol made three API calls. Positive results are memoised; negatives deliberately are not (a symbol can start trading). A user retrying a typo burns quota. A short TTL (say 60 s) for negatives would fix it.

**L2. No fallback if the key is revoked or the plan lapses mid-run.** The poller backs off to 120 s and keeps serving the last prices forever with no signal to the UI. `start_market_data` only covers startup. Acceptable for now, but `mode` never changes and there is no staleness indicator. `PriceUpdate.timestamp` does keep ageing, so the frontend could derive staleness.

**L3. Stream coverage is generator-level only.** `stream.py:41` (the route handler) is the one uncovered line. The tests drive `generate_price_events` with a fake request and check route registration, but nothing proves the response headers or that `StreamingResponse` wiring works over HTTP. A single integration test against a running server (or `httpx` with a streaming ASGI transport that supports streaming) belongs in the E2E suite (`PLAN.md` §12 "SSE resilience").

**L4. Stale values repeat on every tick with Massive.** The stream resends the cached update every 500 ms (as the PLAN requires: no diffing). With Massive polling every 15 s, `direction` stays "up" for ~30 consecutive events. The docstring tells clients to flash only on `price` change, but the frontend must actually do that, and a naive implementation will flash continuously. Worth an explicit note in the frontend task.

**L5. `set_tickers` and the simulator `add_ticker` log only on first add.** In `SimulatorDataSource.add_ticker` the log line sits inside `if ticker not in self._cache`. Harmless; the log is just missing when a ticker is re-added over an existing cache entry.

**L6. Writes after `stop()` are possible.** `SimulatorDataSource.add_ticker` after `stop()` still writes to the cache (the loop is stopped, but `add_ticker` seeds directly). `MarketDataSource.stop()` says "the source will not write to the cache again". Unlikely to matter in the app lifecycle; relevant for test isolation.

**L7. `massive` is imported unconditionally.** `factory.py` → `massive_client.py` imports `RESTClient` at top level, so simulator-only runs still need the `massive` wheel to install and import. Already accepted in `MARKET_INTERFACE.md` §2 item 7; noting it because it affects the Docker image size and cold start.

**L8. Timing-based tests.** Several async tests use `asyncio.sleep` windows (e.g. `test_removed_mid_request_is_not_resurrected` uses a 0.1 s blocking fetch and a 0.03 s wait; the SSE heartbeat tests use 10–30 ms intervals). They passed 4 of 4 runs and in a loaded CI container could fail intermittently. Using `asyncio.Event`s instead of sleeps would make them deterministic.

**L9. Documentation drift.**
- `MARKET_DATA_SUMMARY.md` still describes 8 modules and 73 tests, "Polygon.io REST poller", and an old module table (only the status line was updated).
- `MARKET_DATA_DESIGN.md` still specifies `reference_price`, `to_dict()`, one-map-per-frame SSE and `tracking.py`, none of which exist. It is labelled as superseding the archived design, so anyone implementing from it would build the wrong thing.
- `MARKET_SIMULATOR.md` §7 describes "Changes required" that are now done.
- `backend/README.md` module list lacks `errors.py`/`tickers.py`.
- `PLAN.md` §6 still claims 15 s snapshot polling works on the free tier (flagged in `MASSIVE_API.md` §2; unchanged).
- `PLAN.md` §13 "Follow-up work on the completed market data module" is now done and should be removed or marked complete.

**L10. No typed `Direction`.** `PriceUpdate.direction` returns `str`, not `Literal["up","down","flat"]`; a typo in a consumer wouldn't be caught by a type checker.

## Design points that held up

- **Pure parsing function** (`parse_snapshot`) with a fallback chain, tested against real `massive` models; the old `MagicMock` tests could not have caught the `.timestamp` bug. A regression test asserts `last_trade.timestamp` does not exist.
- **Copy-on-poll plus re-check of the tracked set** correctly prevents a removed ticker from reappearing (tested with a slow fetch).
- **Private RNGs** make simulator tests reproducible and independent of global `random` state (tested).
- **`validate_ticker` never conflates a network error with an unknown symbol** (`PriceUnavailable` vs `False`).
- **Router built inside the factory** removes the duplicate-route bug; tested.
- **Exception hierarchy** (`MarketDataError` base; `InvalidTicker`/`UnknownTicker` also `ValueError`) maps cleanly to the PLAN §8 codes. The `# noqa: N818` markers are deliberate: the names come from the design docs.

## Recommended order

1. M1 and M2: small, local, and both reproducible. Add the tests with the fixes.
2. M3: tighten auth detection.
3. Update the stale docs (L9), starting with `PLAN.md` §6/§13 and `MARKET_DATA_DESIGN.md`, so the next agent doesn't implement the superseded design.
4. M4 when the portfolio/watchlist routes are built.
5. L1, L2, L8 as time allows.
