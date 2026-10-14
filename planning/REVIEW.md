# Code Review

Reviewed all changes on `market-data-contract-update` against `main` (`0a25e6a..9109dcb`), covering the market data implementation, tests, configuration, and planning documentation. This was a static review; I did not run tests.

## Findings

### [P1] Invalid Massive credentials can pass startup when no tickers are tracked

`MassiveDataSource.start()` relies on `_poll_once(raise_auth=True)` to check the key and plan, but `_poll_once()` returns immediately when `_tickers` is empty. With an empty watchlist/position set, the app accepts a rejected key and reports Massive as the active source. The simulator fallback in `start_market_data()` is never reached, and the key remains unchecked until a ticker is added.

**Fix:** perform an authenticated probe regardless of the tracked set, or defer source selection and fallback until the first ticker is validated.

### [P2] Startup fails if a persisted ticker is malformed

`MassiveDataSource.start()` passes every startup ticker to `_track()`, where `normalize_ticker()` raises `InvalidTicker`. `start_market_data()` catches only `MarketDataAuthError`, so one stale or malformed database row aborts application startup. `SimulatorDataSource.start()` and `MarketDataSource.set_tickers()` skip invalid tickers instead.

**Fix:** catch `InvalidTicker` in Massive startup, log and skip the ticker, and continue starting the source.

### [P2] Adding a batch of Massive tickers triggers a batch of full-set API polls

Each new ticker in `MassiveDataSource.add_ticker()` starts an out-of-cycle `_poll_once()`. Reconciliation through `set_tickers()` adds tickers sequentially, so adding N tickers schedules N requests, each fetching the entire tracked set. Startup additions are batched before the first poll, but later bulk changes can consume quota and provoke rate limiting.

**Fix:** coalesce pending extra polls or add a batch reconciliation operation that tracks all new symbols before polling once.

### [P3] Auth detection can classify rate-limit errors as rejected credentials

`_is_auth_error()` treats any exception text containing `"api key"` or `"apikey"` as an authentication failure. A rate-limit response mentioning the key can therefore trigger permanent simulator fallback during startup instead of retry/backoff. The structured Massive error response should determine this classification.

**Fix:** classify from HTTP status and structured error code (for example `NOT_AUTHORIZED`) rather than generic message substrings.

## Review notes

The changes implement the updated cache/SSE payload, baseline prices, ticker normalization, source reconciliation, and simulator constraints. I did not identify additional actionable defects in those paths during this static review. Live Massive API behavior and end-to-end SSE transport were not verified.
