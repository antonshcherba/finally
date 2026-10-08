# FinAlly — AI Trading Workstation

## Project Specification

## 1. Vision

FinAlly (Finance Ally) is a visually stunning AI-powered trading workstation that streams live market data, lets users trade a simulated portfolio, and integrates an LLM chat assistant that can analyze positions and execute trades on the user's behalf. It looks and feels like a modern Bloomberg terminal with an AI copilot.

This is the capstone project for an agentic AI coding course. It is built entirely by Coding Agents demonstrating how orchestrated AI agents can produce a production-quality full-stack application. Agents interact through files in `planning/`.

## 2. User Experience

### First Launch

The user runs a single Docker command (or a provided start script). A browser opens to `http://localhost:8000`. No login, no signup. They immediately see:

- A watchlist of 10 default tickers with live-updating prices in a grid
- $10,000 in virtual cash
- A dark, data-rich trading terminal aesthetic
- An AI chat panel ready to assist

### What the User Can Do

- **Watch prices stream** — prices flash green (uptick) or red (downtick) with subtle CSS animations that fade
- **View sparkline mini-charts** — price action beside each ticker in the watchlist, accumulated on the frontend from the SSE stream since page load (sparklines fill in progressively)
- **Click a ticker** to see a larger detailed chart in the main chart area
- **Buy and sell shares** — market orders only, instant fill at current price, no fees, no confirmation dialog
- **Monitor their portfolio** — a heatmap (treemap) showing positions sized by weight and colored by P&L, plus a P&L chart tracking total portfolio value over time
- **View a positions table** — ticker, quantity, average cost, current price, unrealized P&L, % change
- **Chat with the AI assistant** — ask about their portfolio, get analysis, and have the AI execute trades and manage the watchlist through natural language
- **Manage the watchlist** — add/remove tickers manually or via the AI chat
- **Reset** — a header button restores the initial state ($10,000 cash, no positions, default watchlist, empty history and chat), after a confirmation prompt since it is destructive

### Visual Design

- **Dark theme**: backgrounds around `#0d1117` or `#1a1a2e`, muted gray borders, no pure black
- **Price flash animations**: brief green/red background highlight on price change, fading over ~500ms via CSS transitions
- **Connection status indicator**: a small colored dot (green = connected, yellow = reconnecting, red = disconnected) visible in the header
- **Professional, data-dense layout**: inspired by Bloomberg/trading terminals — every pixel earns its place
- **Responsive but desktop-first**: optimized for wide screens, functional on tablet

### Color Scheme
- Accent Yellow: `#ecad0a`
- Blue Primary: `#209dd7`
- Purple Secondary: `#753991` (submit buttons)

## 3. Architecture Overview

### Single Container, Single Port

```
┌─────────────────────────────────────────────────┐
│  Docker Container (port 8000)                   │
│                                                 │
│  FastAPI (Python/uv)                            │
│  ├── /api/*          REST endpoints             │
│  ├── /api/stream/*   SSE streaming              │
│  └── /*              Static file serving         │
│                      (Next.js export)            │
│                                                 │
│  SQLite database (volume-mounted)               │
│  Background task: market data polling/sim        │
└─────────────────────────────────────────────────┘
```

- **Frontend**: Next.js with TypeScript, built as a static export (`output: 'export'`), served by FastAPI as static files
- **Backend**: FastAPI (Python), managed as a `uv` project
- **Database**: SQLite, single file at `db/finally.db`, volume-mounted for persistence
- **Real-time data**: Server-Sent Events (SSE) — simpler than WebSockets, one-way server→client push, works everywhere
- **AI integration**: LiteLLM → OpenRouter (Cerebras for fast inference), with structured outputs for trade execution
- **Market data**: Simulator by default; real data via the Massive API if a key is provided (optional, not on the critical path)

### Why These Choices

| Decision | Rationale |
|---|---|
| SSE over WebSockets | One-way push is all we need; simpler, no bidirectional complexity, universal browser support |
| Static Next.js export | Single origin, no CORS issues, one port, one container, simple deployment |
| SQLite over Postgres | No auth = no multi-user = no need for a database server; self-contained, zero config |
| Single Docker container | Students run one command; no docker-compose for production, no service orchestration |
| uv for Python | Fast, modern Python project management; reproducible lockfile; what students should learn |
| Market orders only | Eliminates order book, limit order logic, partial fills — dramatically simpler portfolio math |

---

## 4. Directory Structure

```
finally/
├── frontend/                 # Next.js TypeScript project (static export)
├── backend/                  # FastAPI uv project (Python)
│   └── app/storage/          # Schema definitions, seed data, DB init logic
├── planning/                 # Project-wide documentation for agents
│   ├── PLAN.md               # This document
│   └── ...                   # Additional agent reference docs
├── scripts/
│   ├── start_mac.sh          # Launch Docker container (macOS/Linux)
│   ├── stop_mac.sh           # Stop Docker container (macOS/Linux)
│   ├── start_windows.ps1     # (optional, lower priority) Launch Docker container (Windows PowerShell)
│   └── stop_windows.ps1      # (optional, lower priority) Stop Docker container (Windows PowerShell)
├── test/                     # Playwright E2E tests + docker-compose.test.yml
├── db/                       # Volume mount target (SQLite file lives here at runtime)
│   └── .gitkeep              # Directory exists in repo; finally.db is gitignored
├── Dockerfile                # Multi-stage build (Node → Python)
├── docker-compose.yml        # Optional convenience wrapper
├── .env                      # Environment variables (gitignored, .env.example committed)
└── .gitignore
```

### Key Boundaries

- **`frontend/`** is a self-contained Next.js project. It knows nothing about Python. It talks to the backend via `/api/*` endpoints and `/api/stream/*` SSE endpoints. Internal structure is up to the Frontend Engineer agent.
- **`backend/`** is a self-contained uv project with its own `pyproject.toml`. It owns all server logic including database initialization, schema, seed data, API routes, SSE streaming, market data, and LLM integration. Internal structure is up to the Backend/Market Data agents.
- **`backend/app/storage/`** contains schema SQL definitions and seed logic (named `storage`, not `db`, to avoid confusion with the top-level runtime `db/` directory). The backend initializes the database once at startup (FastAPI lifespan) — creating tables and seeding default data if the SQLite file doesn't exist or is empty.
- **`db/`** at the top level is the runtime volume mount point. The SQLite file (`db/finally.db`) is created here by the backend and persists across container restarts via Docker volume.
- **`planning/`** contains project-wide documentation, including this plan. All agents reference files here as the shared contract.
- **`test/`** contains Playwright E2E tests and supporting infrastructure (e.g., `docker-compose.test.yml`). Unit tests live within `frontend/` and `backend/` respectively, following each framework's conventions.
- **`scripts/`** contains start/stop scripts that wrap Docker commands.

---

## 5. Environment Variables

```bash
# Required for chat: OpenRouter API key for LLM chat functionality
OPENROUTER_API_KEY=your-openrouter-api-key-here

# Optional: Massive (formerly Polygon.io) API key for real market data
# If not set, the built-in market simulator is used (recommended for most users)
MASSIVE_API_KEY=

# Optional: Set to "true" for deterministic mock LLM responses (testing)
LLM_MOCK=false
```

### Behavior

- If `MASSIVE_API_KEY` is set and non-empty → backend uses Massive REST API for market data
- If `MASSIVE_API_KEY` is absent or empty → backend uses the built-in market simulator
- If `LLM_MOCK=true` → backend returns deterministic mock LLM responses (for E2E tests)
- Outside Docker (local dev), the backend reads `.env` from the project root. Inside Docker, variables are supplied by `docker run --env-file .env`; `.env` is never copied into the image (listed in `.dockerignore`). `.env.example` is committed.
- If `OPENROUTER_API_KEY` is missing (and `LLM_MOCK` is not true), the app still starts; `POST /api/chat` returns `503 chat_unavailable` and the chat panel shows a clear "chat is not configured" message

---

## 6. Market Data

### Two Implementations, One Interface

Both the simulator and the Massive client implement the same abstract interface. The backend selects which to use based on the environment variable. All downstream code (SSE streaming, price cache, frontend) is agnostic to the source.

### Simulator (Default)

- Generates prices using geometric Brownian motion (GBM) with configurable drift and volatility per ticker
- Updates at ~500ms intervals
- Correlated moves across tickers (e.g., tech stocks move together)
- Occasional random "events" — sudden 2-5% moves on a ticker for drama
- Starts from realistic seed prices (e.g., AAPL ~$190, GOOGL ~$175, etc.)
- Supports a fixed set of known tickers: the 10 default tickers, each with a seed price and GBM params. Tickers outside this set are rejected — see Ticker Validation below. (Supporting more tickers later is just adding entries to `seed_prices.py`.)
- Runs as an in-process background task — no external dependencies

### Massive API (Optional)

This is optional and not on the critical path: the simulator satisfies all required behavior, and the client already exists in `backend/app/market/massive_client.py`. No new Massive work is planned beyond what is described here.

- REST API polling (not WebSocket) — simpler, works on all tiers
- Polls for the union of tracked tickers (see Tracked Tickers) on a configurable interval, using a single call per poll to the full-market stocks snapshot endpoint, filtered with the `tickers` query parameter, to stay within the free-tier limit
- A ticker is valid only if the API returns data for it
- When the market is closed, prices simply don't move; this is acceptable and no special UI is shown
- Free tier (5 calls/min): poll every 15 seconds
- Paid tiers: poll every 2-15 seconds depending on tier
- Parses REST response into the same format as the simulator

### Tracked Tickers

The price source tracks the **union of the watchlist and open positions**. Removing a ticker from the watchlist while holding a position does not stop its pricing (needed for position valuation and the heatmap).

### Ticker Validation

Tickers are trimmed and uppercased. Unknown symbols are rejected (`400 unknown_ticker`) by both the watchlist add endpoint and the trade endpoint: the simulator rejects anything outside its known set; the Massive client rejects symbols for which the API returns no data.

### Baseline Price and Change %

Each ticker has a `baseline_price` — the simulator's seed price (with Massive: the previous day's close from the snapshot response). "Daily change %" shown in the UI is `(price - baseline_price) / baseline_price * 100`. The baseline is provided by the server so the frontend does not need history.

### Shared Price Cache

- A single background task (simulator or Massive poller) writes to an in-memory price cache
- The cache holds the latest price, previous price, and timestamp for each ticker
- SSE streams read from this cache and push updates to connected clients
- This architecture supports future multi-user scenarios without changes to the data layer

### SSE Streaming

- Endpoint: `GET /api/stream/prices`
- Long-lived SSE connection; client uses native `EventSource` API
- Server pushes price updates for all tickers known to the system at a regular cadence (~500ms) — in the single-user model this is equivalent to the user's watchlist
- Every tick, all tracked tickers are sent (simple, stateless for the client; no diffing)
- Each SSE event is a JSON object: `{"ticker": "AAPL", "price": 190.12, "previous_price": 190.05, "baseline_price": 190.0, "timestamp": "<ISO>", "direction": "up" | "down" | "flat"}`
- Client handles reconnection automatically (EventSource has built-in retry)

---

## 7. Database

### SQLite with Startup Initialization

The backend checks for the SQLite database once at startup (FastAPI lifespan, before background tasks start). If the file doesn't exist or tables are missing, it creates the schema and seeds default data. This means:

- No separate migration step
- No manual database setup
- Fresh Docker volumes start with a clean, seeded database automatically

### Schema

The app is single-user, so the schema has no `user_id` columns; multi-user support, if ever needed, is a later migration.

Money and quantities are stored as floating-point `REAL`. Rounding error is accepted for this simulation; displayed values are rounded to cents.

**account** — Single-row table holding cash balance
- `id` INTEGER PRIMARY KEY, `CHECK (id = 1)`
- `cash_balance` REAL (default: `10000.0`)
- `created_at` TEXT (ISO timestamp)

**watchlist** — Tickers the user is watching
- `ticker` TEXT PRIMARY KEY
- `added_at` TEXT (ISO timestamp)

**positions** — Current holdings (one row per ticker)
- `ticker` TEXT PRIMARY KEY
- `quantity` REAL (fractional shares supported)
- `avg_cost` REAL
- `updated_at` TEXT (ISO timestamp)

**trades** — Trade history (append-only log)
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `ticker` TEXT
- `side` TEXT (`"buy"` or `"sell"`)
- `quantity` REAL (fractional shares supported)
- `price` REAL
- `executed_at` TEXT (ISO timestamp)

**portfolio_snapshots** — Portfolio value over time (for P&L chart). Recorded every 30 seconds by a background task (regardless of whether there are positions), and immediately after each trade execution. Retention: only the most recent 5,000 snapshots are kept (older rows are pruned on insert), and `/api/portfolio/history` returns them all.
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `total_value` REAL
- `recorded_at` TEXT (ISO timestamp)

**chat_messages** — Conversation history with LLM
- `id` INTEGER PRIMARY KEY AUTOINCREMENT
- `role` TEXT (`"user"` or `"assistant"`)
- `content` TEXT
- `actions` TEXT (JSON — the executed/failed action results, as returned by `/api/chat`; null for user messages)
- `created_at` TEXT (ISO timestamp)

### Default Seed Data

- One account row: `id=1`, `cash_balance=10000.0`
- Ten watchlist entries: AAPL, GOOGL, MSFT, AMZN, TSLA, NVDA, META, JPM, V, NFLX

---

## 8. API Endpoints

### Error Format

All errors use the same shape and an appropriate HTTP status:

```json
{"error": {"code": "insufficient_cash", "message": "Need $1,900.00 but only $1,200.00 available"}}
```

| Code | Status | Meaning |
|---|---|---|
| `invalid_ticker` | 400 | Not 1–5 letters after trimming/uppercasing |
| `unknown_ticker` | 400 | Valid format but not supported by the market data source |
| `invalid_quantity` | 400 | Not a finite number > 0, below the minimum of 0.0001, or more than 4 decimal places |
| `invalid_side` | 400 | `side` not `"buy"` or `"sell"` |
| `insufficient_cash` | 400 | Buy cost exceeds cash balance |
| `insufficient_shares` | 400 | Sell quantity exceeds shares held |
| `already_in_watchlist` / `not_in_watchlist` | 409 / 404 | Watchlist add of existing / remove of missing ticker |
| `price_unavailable` | 503 | No price yet for the ticker |
| `chat_unavailable` | 503 | No API key configured and mock mode off |

### Market Data
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/stream/prices` | SSE stream of live price updates (event payload defined in §6) |

### Portfolio
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/portfolio` | Current positions, cash balance, total value, unrealized P&L |
| POST | `/api/portfolio/trade` | Execute a trade: `{ticker, quantity, side}` |
| GET | `/api/portfolio/history` | Portfolio value snapshots over time (for P&L chart) |

`GET /api/portfolio` response (also returned as `portfolio` by the trade endpoint):

```json
{
  "cash_balance": 8100.0,
  "total_value": 10050.0,
  "total_unrealized_pnl": 50.0,
  "positions": [
    {"ticker": "AAPL", "quantity": 10, "avg_cost": 190.0, "current_price": 195.0,
     "market_value": 1950.0, "unrealized_pnl": 50.0, "unrealized_pnl_pct": 2.63}
  ]
}
```

`POST /api/portfolio/trade` success (200): `{"trade": {"ticker", "side", "quantity", "price", "executed_at"}, "portfolio": <portfolio object>}`. Selling the entire position deletes the position row.

`GET /api/portfolio/history` response: `{"snapshots": [{"total_value": 10000.0, "recorded_at": "<ISO>"}, ...]}` (oldest first).

### Watchlist
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/watchlist` | Current watchlist tickers with latest prices |
| POST | `/api/watchlist` | Add a ticker: `{ticker}` |
| DELETE | `/api/watchlist/{ticker}` | Remove a ticker |

`GET /api/watchlist` response: `{"watchlist": [{"ticker": "AAPL", "price": 190.12, "baseline_price": 190.0, "change_pct": 0.06}, ...]}`. The price here is only for first paint; once the SSE stream delivers, the stream is the single source of truth. `POST` returns the created entry (same shape, 201). `DELETE` returns 204.

### Chat
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/chat` | Send a message `{message}`, receive complete JSON response (message + executed actions) |

Response:

```json
{
  "message": "Bought 10 AAPL for you.",
  "actions": [
    {"type": "buy", "ticker": "AAPL", "quantity": 10, "status": "executed", "price": 190.12},
    {"type": "buy", "ticker": "TSLA", "quantity": 500, "status": "failed", "error": {"code": "insufficient_cash", "message": "..."}},
    {"type": "watch_add", "ticker": "NFLX", "status": "executed"}
  ]
}
```

`type` is one of `buy`, `sell`, `watch_add`, `watch_remove`. `quantity` and `price` apply to trades only.

### System
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/health` | Health check (for Docker/deployment) |
| POST | `/api/reset` | Reset to initial state: cash $10,000, no positions, default watchlist, trades/snapshots/chat cleared, one fresh snapshot recorded |

---

## 9. LLM Integration

When writing code to make calls to LLMs, use cerebras-inference skill to use LiteLLM via OpenRouter to the `openrouter/openai/gpt-oss-120b` model with Cerebras as the inference provider. Structured Outputs should be used to interpret the results.

There is an OPENROUTER_API_KEY in the .env file in the project root.

### How It Works

When the user sends a chat message, the backend:

1. Loads the user's current portfolio context (cash, positions with P&L, watchlist with live prices, total portfolio value)
2. Loads the most recent 20 messages from the `chat_messages` table
3. Constructs a prompt with a system message, portfolio context, conversation history, and the user's new message
4. Calls the LLM via LiteLLM → OpenRouter, requesting structured output, using the cerebras-inference skill
5. Parses the complete structured JSON response
6. Auto-executes any trades or watchlist changes specified in the response
7. Stores the message and executed actions in `chat_messages`
8. Returns the complete JSON response to the frontend (no token-by-token streaming — Cerebras inference is fast enough that a loading indicator is sufficient)

### Structured Output Schema

The LLM is instructed to respond with JSON matching this schema:

```json
{
  "message": "Your conversational response to the user",
  "actions": [
    {"type": "buy", "ticker": "AAPL", "quantity": 10},
    {"type": "watch_add", "ticker": "NFLX"}
  ]
}
```

- `message` (required): The conversational text shown to the user
- `actions` (optional): Ordered list of actions to auto-execute, one of `buy`, `sell`, `watch_add`, `watch_remove` (`quantity` required for `buy`/`sell`). Trades go through the same validation as manual trades (sufficient cash for buys, sufficient shares for sells)

### Auto-Execution

Trades specified by the LLM execute automatically — no confirmation dialog. This is a deliberate design choice:
- It's a simulated environment with fake money, so the stakes are zero
- It creates an impressive, fluid demo experience
- It demonstrates agentic AI capabilities — the core theme of the course

The flow is single-shot (one LLM call per user message). If an action fails validation (e.g., insufficient cash, unknown ticker), the failure is reported in the response (`status: "failed"` with an `error`) and shown inline by the frontend next to the assistant's message. The LLM is not called a second time to react to failures. The same service function validates and executes trades for both `/api/portfolio/trade` and the chat flow, so the two paths cannot diverge.

### System Prompt Guidance

The LLM should be prompted as "FinAlly, an AI trading assistant" with instructions to:
- Analyze portfolio composition, risk concentration, and P&L
- Suggest trades with reasoning
- Execute trades when the user asks or agrees
- Manage the watchlist proactively
- Be concise and data-driven in responses
- Always respond with valid structured JSON

### LLM Mock Mode

When `LLM_MOCK=true`, the backend returns deterministic mock responses instead of calling OpenRouter. The mock is keyword-driven on the (lowercased) user message, first match wins:

| Message contains | Mock response |
|---|---|
| `buy` | message + buy 1 AAPL |
| `sell` | message + sell 1 AAPL |
| `remove` | message + remove V from the watchlist |
| `watch` | message + add V to the watchlist |
| anything else | plain message, no actions |

This enables:
- Fast, free, reproducible E2E tests
- Development without an API key
- CI/CD pipelines

---

## 10. Frontend Design

### Layout

The frontend is a single-page application with a dense, terminal-inspired layout. The specific component architecture and layout system is up to the Frontend Engineer, but the UI should include these elements:

- **Watchlist panel** — grid/table of watched tickers with: ticker symbol, current price (flashing green/red on change), change % vs. the baseline price (see §6), and a sparkline mini-chart (accumulated from SSE since page load)
- **Main chart area** — larger chart for the currently selected ticker, with at minimum price over time. Clicking a ticker in the watchlist selects it here.
- **Portfolio heatmap** — treemap visualization where each rectangle is a position, sized by portfolio weight, colored by P&L (green = profit, red = loss)
- **P&L chart** — line chart showing total portfolio value over time, using data from `portfolio_snapshots`
- **Positions table** — tabular view of all positions: ticker, quantity, avg cost, current price, unrealized P&L, % change
- **Trade bar** — simple input area: ticker field, quantity field, buy button, sell button. Market orders, instant fill.
- **AI chat panel** — docked/collapsible sidebar. Message input, scrolling conversation history, loading indicator while waiting for LLM response. Trade executions and watchlist changes shown inline as confirmations.
- **Header** — portfolio total value (updating live), connection status indicator, cash balance, reset button

### Technical Notes

- Use `EventSource` for SSE connection to `/api/stream/prices`
- Lightweight Charts (canvas) for the main chart, sparklines and P&L line chart; Recharts `Treemap` for the portfolio heatmap
- Dev workflow: run `next dev` (port 3000) with `rewrites` in `next.config` proxying `/api/*` to the FastAPI dev server (port 8000); the rewrites apply only in dev, and are conditional on `NODE_ENV` so they don't break `output: 'export'` builds
- Price flash effect: on receiving a new price, briefly apply a CSS class with background color transition, then remove it
- All API calls go to the same origin (`/api/*`) — no CORS configuration needed
- Tailwind CSS for styling with a custom dark theme

---

## 11. Docker & Deployment

### Multi-Stage Dockerfile

```
Stage 1: Node 20 slim
  - Copy frontend/
  - npm install && npm run build (produces static export)

Stage 2: Python 3.12 slim
  - Install uv
  - Copy backend/
  - uv sync (install Python dependencies from lockfile)
  - Copy frontend build output into a static/ directory
  - Expose port 8000
  - CMD: uvicorn serving FastAPI app
```

FastAPI serves the static frontend files and all API routes on port 8000.

### Docker Volume

The SQLite database persists via a named Docker volume:

```bash
docker run -v finally-data:/app/db -p 8000:8000 --env-file .env finally
```

The `db/` directory in the project root maps to `/app/db` in the container. The backend writes `finally.db` to this path.

### Start/Stop Scripts

**`scripts/start_mac.sh`** (macOS/Linux):
- Builds the Docker image if not already built (or if `--build` flag passed)
- Runs the container with the volume mount, port mapping, and `.env` file
- Prints the URL to access the app
- Optionally opens the browser

**`scripts/stop_mac.sh`** (macOS/Linux):
- Stops and removes the running container
- Does NOT remove the volume (data persists)

**`scripts/start_windows.ps1`** / **`scripts/stop_windows.ps1`**: PowerShell equivalents for Windows (optional, lower priority; untested on CI).

All scripts should be idempotent — safe to run multiple times.

### Optional Cloud Deployment

The container is designed to deploy to AWS App Runner, Render, or any container platform. A Terraform configuration for App Runner may be provided in a `deploy/` directory as a stretch goal, but is not part of the core build.

---

## 12. Testing Strategy

### Unit Tests (within `frontend/` and `backend/`)

**Backend (pytest)**:
- Market data: simulator generates valid prices, GBM math is correct, unknown tickers are rejected, `baseline_price` is provided; Massive API response parsing works and both implementations conform to the abstract interface
- Portfolio: trade execution logic, P&L calculations, edge cases (selling more than owned, buying with insufficient cash, selling at a loss)
- LLM: structured output parsing handles all valid schemas, graceful handling of malformed responses, trade validation within chat flow
- API routes: correct status codes, response shapes, error codes from §8 (invalid/zero/negative/over-precise quantity, unknown ticker, insufficient cash/shares), reset, startup initialization
- Chat: mock keyword triggers, failed trades reported inline, missing API key returns `chat_unavailable`

**Frontend (React Testing Library or similar)**:
- Component rendering with mock data
- Price flash animation triggers correctly on price changes
- Watchlist CRUD operations
- Portfolio display calculations
- Chat message rendering and loading state

### E2E Tests (in `test/`)

**Infrastructure**: A separate `docker-compose.test.yml` in `test/` that spins up the app container plus a Playwright container. This keeps browser dependencies out of the production image.

**Environment**: Tests run with `LLM_MOCK=true` by default for speed and determinism.

**Key Scenarios**:
- Fresh start: default watchlist appears, $10k balance shown, prices are streaming
- Remove a default ticker from the watchlist and add it back; adding an unknown ticker shows an error
- Buy shares: cash decreases, position appears, portfolio updates
- Sell shares: cash increases, position updates or disappears
- Portfolio visualization: heatmap renders with correct colors, P&L chart has data points
- AI chat (mocked): send "buy", receive a response, trade execution appears inline
- Reset: after trading, reset restores $10k, empty positions and default watchlist
- SSE resilience: disconnect and verify reconnection

---

## 13. Decision Log & Open Questions

### Decisions incorporated above

| Topic | Decision |
|---|---|
| Change % baseline | Seed price (Massive: previous day's close), served by the backend |
| Unknown tickers | Rejected by watchlist and trade endpoints; simulator knows only the 10 default tickers for now |
| Tracked tickers | Watchlist ∪ open positions |
| Validation and errors | Defaults and error shape defined in §8 |
| Money | Floats, rounded to cents for display |
| Chat failures | Reported in the response, no second LLM call |
| Chat schema | Single ordered `actions` list (`buy`, `sell`, `watch_add`, `watch_remove`) |
| History window | Fixed 20 messages |
| Reset | `POST /api/reset` plus header button with confirmation prompt |
| Market closed | Acceptable, no special UI |
| DB init | At startup (lifespan) |
| Schema | No `user_id`; single-row `account` table; ticker primary keys for `watchlist`/`positions`; integer keys elsewhere |
| Code directory | `backend/app/storage/` (not `backend/db/`) |
| Charts | Lightweight Charts + Recharts Treemap |
| Snapshot retention | Latest 5,000 |
| Secrets | `.env` not baked into image; app runs without API key, chat returns `chat_unavailable` |
| Massive | Optional, not on the critical path; "Massive (formerly Polygon.io)"; full-market snapshot endpoint filtered by tickers |
| Windows scripts | Optional, lower priority |
| `docker-compose.yml` | Kept as an optional convenience wrapper |

### Follow-up work on the completed market data module

The module under `backend/app/market/` predates these decisions and needs small changes:
- Reject tickers outside the known set in the simulator (it currently accepts any ticker with default GBM params)
- Add `baseline_price` to `PriceUpdate` and the SSE payload
- Support the tracked-ticker union (watchlist ∪ positions) via `add_ticker` / `remove_ticker`
