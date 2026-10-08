# Code Review

Reviewed commit `777f7e0` (`Align market data module with PLAN contract`) against its parent, including the market data implementation, tests, environment example, and planning/backend documentation. This was a static review; I did not run tests.

## Findings

### [Medium] Startup fails on malformed persisted tickers

`MassiveDataSource.start()` passes every startup ticker directly to `_track()`, which raises `InvalidTicker` for malformed values. `start_market_data()` only catches `MarketDataAuthError`, so one stale or malformed database row prevents application startup. The simulator and `set_tickers()` already skip invalid tickers.

**Fix:** normalize and skip invalid tickers during Massive startup, logging the rejected value.

### [Medium] Reconciliation does not validate Massive tickers

`MarketDataSource.set_tickers()` describes skipping unsupported symbols, but it only calls `add_ticker()`. Massive's `add_ticker()` checks syntax and tracks the symbol without calling `validate_ticker()`. Unsupported or delisted symbols from a watchlist/position set are therefore tracked indefinitely without prices; callers using `set_tickers()` can bypass the advertised unknown-ticker check.

**Fix:** validate each new symbol before adding it, preserving `PriceUnavailable` for lookup failures and skipping only a confirmed unknown symbol.

### [Medium] Adding several Massive tickers triggers several full-set polls

Each new Massive ticker starts an out-of-cycle poll. A batch reconcile that adds N tickers therefore schedules N requests, each fetching the entire tracked set. This can rapidly consume API quota during startup or bulk changes and cause rate limiting.

**Fix:** batch additions and poll once, or coalesce pending out-of-cycle polls.

### [Medium] Snapshot absence can reject a valid Massive ticker

`validate_ticker()` returns `False` when the snapshot endpoint returns no usable snapshot for the symbol. A valid symbol may have no current snapshot data (for example, before its first trade or when the endpoint omits it), so `require_known()` then reports `UnknownTicker` instead of accepting the symbol or returning an indeterminate/unavailable result. The API reference and design docs describe this ambiguity.

**Fix:** validate through a symbol/reference endpoint whose result does not depend on current quote availability, or preserve an indeterminate state instead of treating missing snapshot data as proof the symbol is unknown.

### [Low] Auth detection can mistake rate-limit messages for rejected credentials

`_is_auth_error()` treats any exception text containing “api key” or “apikey” as an auth failure. A rate-limit response whose message mentions the key can trigger simulator fallback during startup instead of normal retry/backoff.

**Fix:** classify by response status and structured error code (such as `NOT_AUTHORIZED`) rather than generic message substrings.

### [Low] Empty startup ticker sets do not check Massive access

`_poll_once()` returns before making a request when there are no tracked tickers. With an empty watchlist/position set, a rejected key or unsupported plan is not detected and `start_market_data()` returns a Massive source instead of falling back. The source also cannot distinguish this state until tickers are later added.

**Fix:** perform an authenticated startup probe independent of the tracked set, or defer source selection and fallback until the first ticker is validated.

## Review notes

The cache snapshot/version locking, snapshot parsing fallback chain, timestamp normalization, simulator ticker restrictions, and SSE payload shape are consistent with the changed code and its updated documentation. The Massive field parsing and HTTP streaming integration still need live/API or end-to-end verification; this review did not run tests.
