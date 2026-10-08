# Massive API Reference (formerly Polygon.io)

How FinAlly retrieves real-time and end-of-day stock prices for multiple tickers from Massive. This is a reference for agents implementing `backend/app/market/massive_client.py`; the unified design built on it is in `MARKET_INTERFACE.md`.

**Sources and confidence.** Endpoint paths, parameters, response fields and plan tiers come from the Massive docs (massive.com/docs/rest/stocks, pricing page, accessed 2026-10). Python client names (`RESTClient`, model attributes, retry behaviour) were verified by installing the `massive` package and parsing sample payloads through its models. **No live API call was made** (no key available in the research environment), so items marked *(unverified live)* should be confirmed against the first real response.

---

## 1. Essentials

| Item | Value |
|---|---|
| Base URL | `https://api.massive.com` (legacy `api.polygon.io` still works) |
| Python package | `massive` (`uv add massive`); sync only, built on `urllib3` |
| Auth | `Authorization: Bearer <key>` header (the client does this) **or** `?apiKey=<key>` query param |
| Env var | `RESTClient()` with no args reads `MASSIVE_API_KEY` itself |
| Ticker case | Case-sensitive; always send uppercase |
| Timestamps | Mixed units, see §6. Never assume one unit |
| Errors | Any non-200 → `massive.exceptions.BadResponse(<response body text>)`. The HTTP status is **not** exposed; parse the JSON body (`{"status": "NOT_AUTHORIZED", "message": ...}`) |
| Retries | Client retries 3× with backoff on 413/429/499/500/502/503/504 (tunable via `retries=`). Timeouts: `connect_timeout=10`, `read_timeout=10` |
| Pagination | `list_*` methods follow `next_url` automatically; `get_*` methods are single calls |

## 2. Plans: the constraint that shapes the design

| Plan | Price | Rate limit | Data | History | Snapshots | Last trade |
|---|---|---|---|---|---|---|
| Stocks **Basic** | Free | **5 calls/min** | End of day | 2 yr | **No** | No |
| Stocks Starter | $29/mo | Unlimited | 15-min delayed | 5 yr | Yes | No |
| Stocks Developer | $79/mo | Unlimited | 15-min delayed | 10 yr | Yes | Yes (delayed) |
| Stocks Advanced | $199/mo | Unlimited | Real-time | 20+ yr | Yes | Yes |

Aggregates, previous-day bar, grouped daily and daily open/close are on **all** plans.

> **Conflict with PLAN.md.** PLAN.md §6 says the free tier can poll the full-market snapshot every 15 s. Per Massive's docs, **snapshots are not included in Stocks Basic**; a free key will get a `NOT_AUTHORIZED`/403-style error from `/v2/snapshot/...`. "Real-time" polling therefore needs Starter or above (15-min delayed) or Advanced (true real-time). On a free key the only live-ish option is end-of-day data. See `MARKET_INTERFACE.md` §7 for how the design handles this. PLAN.md should be corrected.

## 3. Endpoint map: which call for which job

| Need | Endpoint | Calls for N tickers | Plans |
|---|---|---|---|
| Live price, many tickers | `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=A,B,C` | **1** | Starter+ |
| Live price, one ticker | `GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` | 1 per ticker | Starter+ |
| EOD OHLC for the whole market on a date | `GET /v2/aggs/grouped/locale/us/market/stocks/{date}` | **1** | All |
| Previous close, one ticker | `GET /v2/aggs/ticker/{ticker}/prev` | 1 per ticker | All |
| OHLC for a ticker on a date (+pre/after market) | `GET /v1/open-close/{ticker}/{date}` | 1 per ticker | All |
| Historical bars (charts) | `GET /v2/aggs/ticker/{ticker}/range/{mult}/{timespan}/{from}/{to}` | 1 per ticker | All |
| Last trade | `GET /v2/last/trade/{ticker}` | 1 per ticker | Developer+ |

For a multi-ticker app the two that matter are the **filtered full-market snapshot** (live) and **grouped daily** (end of day). Everything else is per-ticker and burns the free tier's 5 calls/min.

---

## 4. Real-time: Full Market Snapshot (primary)

`GET /v2/snapshot/locale/us/markets/stocks/tickers`

| Param | Type | Notes |
|---|---|---|
| `tickers` | comma-separated string | Case-sensitive. **Empty/omitted returns the entire market (10,000+ tickers); always pass it.** Unknown symbols are expected to be silently omitted from the response *(unverified live)*, which is how FinAlly validates tickers |
| `include_otc` | bool, default `false` | |

Snapshot data is cleared overnight (docs say 3:30 AM ET; the client docstring says midnight) and repopulates from ~4:00 AM ET as exchanges report. Before a ticker's first trade of the day, `lastTrade`/`day` may be empty or stale.

### Raw response

```json
{
  "status": "OK",
  "count": 2,
  "tickers": [
    {
      "ticker": "AAPL",
      "todaysChange": 1.2,
      "todaysChangePerc": 0.63,
      "updated": 1700000000000000000,
      "fmv": 191.05,
      "day":     {"o": 190.0, "h": 192.0, "l": 189.0, "c": 191.0, "v": 1000, "vw": 190.5},
      "prevDay": {"o": 188.0, "h": 190.0, "l": 187.0, "c": 189.8, "v": 900,  "vw": 189.0},
      "min":     {"av": 5000, "o": 191.0, "h": 191.2, "l": 190.9, "c": 191.1, "v": 100, "vw": 191.0, "t": 1700000000000, "n": 10},
      "lastTrade": {"p": 191.1, "s": 100, "t": 1700000000000000000, "x": 4, "i": "1", "c": [12]},
      "lastQuote": {"p": 191.0, "P": 191.2, "s": 1, "S": 2, "t": 1700000000000000000}
    }
  ]
}
```

| Object | Fields |
|---|---|
| `day` | `o h l c v vw`: today's running bar |
| `prevDay` | same shape: **previous session's bar; `prevDay.c` is our baseline price** |
| `min` | latest minute bar + `av` (accumulated volume), `t` (ms), `n` (trades) |
| `lastTrade` | `p` price, `s` size, `t` SIP time (**ns**), `x` exchange id, `i` trade id, `c` conditions |
| `lastQuote` | `p` bid, `P` ask, `s` bid size, `S` ask size, `t` (**ns**) |
| top level | `todaysChange`, `todaysChangePerc` (vs prev close), `updated` (**ns**), `fmv` (fair market value, only on plans that include it) |

Plan recency: Starter/Developer = 15-minute delayed; Advanced/Business = real-time.

### Python client

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key="...")          # or RESTClient() to read MASSIVE_API_KEY

snaps = client.get_snapshot_all(
    market_type=SnapshotMarketType.STOCKS,
    tickers=["AAPL", "GOOGL", "MSFT"],      # list is joined with "," for you
)                                           # -> list[TickerSnapshot], ONE http call

for s in snaps:
    print(s.ticker, s.last_trade.price, s.prev_day.close, s.todays_change_percent)
```

### Model attribute names: where the old draft was wrong

The client renames the JSON keys. Verified by `TickerSnapshot.from_dict(payload)`:

| JSON | Python attribute |
|---|---|
| `lastTrade.p` / `.s` / `.t` | `last_trade.price` / `.size` / **`.sip_timestamp`** (ns) |
| `lastQuote.p` / `.P` | `last_quote.bid_price` / `.ask_price` |
| `day.*`, `prevDay.*` | `day.open/high/low/close/volume/vwap`, **`prev_day.close`** |
| `min.*` | `min.open/.../close`, `.timestamp` (ms), `.accumulated_volume` |
| `todaysChange`, `todaysChangePerc` | `todays_change`, `todays_change_percent` |
| `updated`, `fmv` | `updated` (ns), `fair_market_value` |

Pitfalls (all present in the current code or earlier drafts):
- `last_trade.timestamp` **does not exist**. It is `sip_timestamp`, in **nanoseconds**.
- `day.previous_close` / `day.change_percent` **do not exist** on `TickerSnapshot.day` (it is a plain `Agg`). Use `prev_day.close` and `todays_change_percent`.
- Every field is `Optional`. Any of `last_trade`, `day`, `prev_day`, `min` can be `None`.

### Choosing the price

```python
def snapshot_price(s) -> float | None:
    """Best available last price; None if the snapshot is empty."""
    for candidate in (
        s.last_trade and s.last_trade.price,
        s.min and s.min.close,
        s.day and s.day.close,
        s.prev_day and s.prev_day.close,
    ):
        if candidate:
            return float(candidate)
    return None
```

Pre-market, a ticker may have no `lastTrade`; falling back to `prev_day.close` keeps it priced (flat) instead of dropping out.

---

## 5. End of day

### 5a. Grouped Daily: whole market in one call (works on the free plan)

`GET /v2/aggs/grouped/locale/us/market/stocks/{date}` with `adjusted` (default true) and `include_otc` (default false).

```json
{"status": "OK", "resultsCount": 9500, "adjusted": true,
 "results": [{"T": "AAPL", "o": 188.0, "h": 190.0, "l": 187.0, "c": 189.8, "v": 5.1e7, "vw": 189.1, "t": 1700000000000, "n": 600000}]}
```

`T` is the ticker; `t` is the bar's start in **ms**. Non-trading days return **no results**, so callers must walk back to the last trading day.

```python
from datetime import date, timedelta

def last_trading_day_closes(client: RESTClient, wanted: set[str], max_back: int = 7) -> dict[str, float]:
    """Previous-session close for `wanted` tickers via ONE grouped call per candidate date."""
    day = date.today()
    for _ in range(max_back):
        day -= timedelta(days=1)
        if day.weekday() >= 5:                      # skip weekends without spending a call
            continue
        bars = client.get_grouped_daily_aggs(day.isoformat())   # list[GroupedDailyAgg]
        closes = {b.ticker: b.close for b in bars if b.ticker in wanted and b.close}
        if closes:                                  # empty on holidays; try the day before
            return closes
    return {}
```

On the free plan "today" is not available until after the close, so start from yesterday as above. Free-plan data is end-of-day only.

### 5b. Previous Day Bar: one ticker

```python
aggs = client.get_previous_close_agg("AAPL")        # list[PreviousCloseAgg]; usually length 1
print(aggs[0].close)                                # attributes: ticker open high low close volume vwap timestamp
```

REST: `GET /v2/aggs/ticker/AAPL/prev` → `{"results": [{"T","o","h","l","c","v","vw","t","n"}]}`. Costs one call per ticker. Fine for validating a single new symbol, too slow for 10 tickers at 5 calls/min.

### 5c. Daily Open/Close: one ticker, one date

```python
d = client.get_daily_open_close_agg("AAPL", "2026-10-07")
print(d.open, d.close, d.pre_market, d.after_hours)  # DailyOpenCloseAgg
```

REST `GET /v1/open-close/{ticker}/{date}` → `{symbol, from, open, high, low, close, volume, preMarket, afterHours}`. Useful when after-hours price matters.

### 5d. Custom bars (historical charts, not needed for MVP)

`GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`; params `adjusted` (true), `sort` (`asc`/`desc`), `limit` (default 5000, max 50000). Bars: `o h l c v vw n t(ms)`. The main chart is built from the SSE stream, so FinAlly does not need this.

```python
bars = list(client.list_aggs("AAPL", 1, "day", "2026-09-01", "2026-09-30", limit=50000))
```

---

## 6. Timestamp units

| Field | Unit |
|---|---|
| snapshot `lastTrade.t`, `lastQuote.t`, top-level `updated` | nanoseconds |
| snapshot `min.t`; all aggregate bar `t`; grouped daily `t` | milliseconds |
| last-trade endpoint `results.t` (SIP), `results.y` (exchange) | nanoseconds |

Because units are mixed and some are *(unverified live)*, normalise by magnitude instead of hard-coding:

```python
def to_epoch_seconds(ts: int | float) -> float:
    if ts > 1e17:  return ts / 1e9     # ns
    if ts > 1e14:  return ts / 1e6     # µs
    if ts > 1e11:  return ts / 1e3     # ms
    return float(ts)                   # already seconds
```

## 7. Error handling

```python
import json
from massive.exceptions import BadResponse

try:
    snaps = client.get_snapshot_all(SnapshotMarketType.STOCKS, tickers=tickers)
except BadResponse as e:
    body = json.loads(str(e)) if str(e).lstrip().startswith("{") else {}
    status = body.get("status")            # e.g. "NOT_AUTHORIZED", "ERROR"
```

| Symptom | Meaning | Action |
|---|---|---|
| `status: NOT_AUTHORIZED` (HTTP 403) | Plan lacks this endpoint, e.g. snapshot on free key | Permanent. Switch mode, do not retry |
| HTTP 401 / auth error | Bad or missing key | Permanent. Log once, fall back |
| HTTP 429 | Rate limited (Basic: 5/min) | Client already retried 3×. Skip this cycle, keep last prices |
| 5xx / network / `urllib3` errors | Transient | Skip cycle, retry next interval |
| `tickers` filtered result missing a symbol | Unknown symbol or no data yet | Treat as "no price", not as an error |

The client is **synchronous**; from asyncio always use `await asyncio.to_thread(...)`.

## 8. Polling cadence

| Plan | Sensible interval | Why |
|---|---|---|
| Basic (EOD only) | do not poll; fetch baselines once at startup | no live data, 5 calls/min |
| Starter / Developer | 5–15 s | data is 15 min delayed anyway; faster polling just repeats values |
| Advanced / Business | 2–5 s | real-time; unlimited calls |

One snapshot call covers every tracked ticker, so cost is independent of watchlist size. Request-URL length limits the ticker count in one call (hundreds are fine; not an issue for a watchlist).

## 9. Open items to confirm with a live key

1. Snapshot `lastTrade.t`/`updated` units are nanoseconds (assumed per docs; `to_epoch_seconds` makes code safe either way).
2. Unknown symbols in the `tickers=` filter are omitted rather than causing an error.
3. Exact error body returned by a free key calling `/v2/snapshot/...`.
4. Whether the Starter plan's 15-minute delayed snapshots still populate `lastTrade` or only `day`/`min`.

Sources: massive.com/docs/rest/stocks (full-market-snapshot, single-ticker-snapshot, custom-bars, daily-market-summary, daily-ticker-summary, previous-day-bar, last-trade), massive.com/pricing, github.com/massive-com/client-python, and the installed `massive` package source.
