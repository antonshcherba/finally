"""Data-access layer over SQLite (stdlib sqlite3, synchronous).

Each method opens a short-lived connection, so a Database instance is safe to share across
threads/async handlers. Calls are fast local I/O; call directly from async code or via
asyncio.to_thread if preferred. All timestamps are UTC ISO strings. Tickers are stored as given
(callers normalize/validate). Rows are returned as plain dicts.

Methods
-------
init()                              create schema + seed defaults if missing (idempotent)
reset()                             restore initial state, record one fresh snapshot
get_cash() -> float
get_account() -> {cash_balance, created_at}
get_watchlist() -> [ticker, ...]    (insertion order)
add_watchlist(ticker)               raises StorageError("already_in_watchlist")
remove_watchlist(ticker)            raises StorageError("not_in_watchlist")
get_positions() -> [{ticker, quantity, avg_cost, updated_at}]
get_position(ticker) -> dict | None
apply_trade(ticker, side, quantity, price) -> trade dict
    Atomic: updates cash, position (avg_cost on buys, deletes row when fully sold) and appends
    to trades. Safety net only: raises StorageError("insufficient_cash"/"insufficient_shares")
    with no changes; the service layer should still validate first for friendly messages.
get_trades(limit=None) -> newest first
add_snapshot(total_value)           prunes to latest 5,000
get_snapshots() -> [{total_value, recorded_at}]  oldest first
add_chat_message(role, content, actions=None) -> id    (actions: JSON-serializable or None)
get_chat_messages(limit=20) -> oldest first, [{id, role, content, actions, created_at}]
"""

import json
import os
import sqlite3
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

DEFAULT_CASH = 10000.0
DEFAULT_WATCHLIST = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]
SNAPSHOT_RETENTION = 5000
_EPS = 1e-9
_SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


class StorageError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.message = message or code


def _now() -> str:
    return datetime.now(UTC).isoformat()


def default_db_path() -> str:
    env = os.environ.get("FINALLY_DB_PATH")
    if env:
        return env
    return str(Path(__file__).resolve().parents[3] / "db" / "finally.db")


class Database:
    def __init__(self, path: str | os.PathLike | None = None):
        self.path = str(path) if path is not None else default_db_path()

    @contextmanager
    def _tx(self):
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")
        finally:
            conn.close()

    def _query(self, sql: str, params=()) -> list[dict]:
        with closing(sqlite3.connect(self.path, timeout=10)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    # -- lifecycle -------------------------------------------------------
    def init(self) -> None:
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._tx() as c:
            for stmt in _SCHEMA.split(";"):
                if stmt.strip():
                    c.execute(stmt)
            self._seed(c)

    @staticmethod
    def _seed(c: sqlite3.Connection) -> None:
        now = _now()
        c.execute(
            "INSERT OR IGNORE INTO account (id, cash_balance, created_at) VALUES (1, ?, ?)",
            (DEFAULT_CASH, now),
        )
        # Seed the watchlist only on first init (empty user-cleared watchlist stays empty).
        if c.execute("SELECT COUNT(*) FROM account WHERE created_at = ?", (now,)).fetchone()[0]:
            c.executemany(
                "INSERT OR IGNORE INTO watchlist (ticker, added_at) VALUES (?, ?)",
                [(t, now) for t in DEFAULT_WATCHLIST],
            )

    def reset(self) -> None:
        now = _now()
        with self._tx() as c:
            for table in ("positions", "trades", "portfolio_snapshots", "chat_messages", "watchlist"):
                c.execute(f"DELETE FROM {table}")
            c.execute("DELETE FROM sqlite_sequence")
            c.execute(
                "INSERT OR REPLACE INTO account (id, cash_balance, created_at) VALUES (1, ?, ?)",
                (DEFAULT_CASH, now),
            )
            c.executemany(
                "INSERT INTO watchlist (ticker, added_at) VALUES (?, ?)",
                [(t, now) for t in DEFAULT_WATCHLIST],
            )
            c.execute(
                "INSERT INTO portfolio_snapshots (total_value, recorded_at) VALUES (?, ?)",
                (DEFAULT_CASH, now),
            )

    # -- account ---------------------------------------------------------
    def get_account(self) -> dict:
        return self._query("SELECT cash_balance, created_at FROM account WHERE id = 1")[0]

    def get_cash(self) -> float:
        return self.get_account()["cash_balance"]

    # -- watchlist -------------------------------------------------------
    def get_watchlist(self) -> list[str]:
        rows = self._query("SELECT ticker FROM watchlist ORDER BY added_at, rowid")
        return [r["ticker"] for r in rows]

    def add_watchlist(self, ticker: str) -> None:
        with self._tx() as c:
            if c.execute("SELECT 1 FROM watchlist WHERE ticker = ?", (ticker,)).fetchone():
                raise StorageError("already_in_watchlist", f"{ticker} is already in the watchlist")
            c.execute("INSERT INTO watchlist (ticker, added_at) VALUES (?, ?)", (ticker, _now()))

    def remove_watchlist(self, ticker: str) -> None:
        with self._tx() as c:
            cur = c.execute("DELETE FROM watchlist WHERE ticker = ?", (ticker,))
            if cur.rowcount == 0:
                raise StorageError("not_in_watchlist", f"{ticker} is not in the watchlist")

    # -- positions & trades ---------------------------------------------
    def get_positions(self) -> list[dict]:
        return self._query(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions ORDER BY ticker"
        )

    def get_position(self, ticker: str) -> dict | None:
        rows = self._query(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions WHERE ticker = ?",
            (ticker,),
        )
        return rows[0] if rows else None

    def apply_trade(self, ticker: str, side: str, quantity: float, price: float) -> dict:
        if side not in ("buy", "sell"):
            raise StorageError("invalid_side", "side must be 'buy' or 'sell'")
        now = _now()
        cost = quantity * price
        with self._tx() as c:
            cash = c.execute("SELECT cash_balance FROM account WHERE id = 1").fetchone()[0]
            pos = c.execute(
                "SELECT quantity, avg_cost FROM positions WHERE ticker = ?", (ticker,)
            ).fetchone()
            held = pos["quantity"] if pos else 0.0
            if side == "buy":
                if cost > cash + _EPS:
                    raise StorageError(
                        "insufficient_cash",
                        f"Need ${cost:,.2f} but only ${cash:,.2f} available",
                    )
                new_qty = held + quantity
                avg = ((held * pos["avg_cost"] if pos else 0.0) + cost) / new_qty
                c.execute("UPDATE account SET cash_balance = ? WHERE id = 1", (cash - cost,))
                c.execute(
                    "INSERT INTO positions (ticker, quantity, avg_cost, updated_at) "
                    "VALUES (?, ?, ?, ?) ON CONFLICT(ticker) DO UPDATE SET "
                    "quantity = excluded.quantity, avg_cost = excluded.avg_cost, "
                    "updated_at = excluded.updated_at",
                    (ticker, new_qty, avg, now),
                )
            else:
                if quantity > held + _EPS:
                    raise StorageError(
                        "insufficient_shares",
                        f"Cannot sell {quantity:g} {ticker}; only {held:g} held",
                    )
                remaining = held - quantity
                c.execute("UPDATE account SET cash_balance = ? WHERE id = 1", (cash + cost,))
                if remaining <= _EPS:
                    c.execute("DELETE FROM positions WHERE ticker = ?", (ticker,))
                else:
                    c.execute(
                        "UPDATE positions SET quantity = ?, updated_at = ? WHERE ticker = ?",
                        (remaining, now, ticker),
                    )
            c.execute(
                "INSERT INTO trades (ticker, side, quantity, price, executed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (ticker, side, quantity, price, now),
            )
        return {
            "ticker": ticker,
            "side": side,
            "quantity": quantity,
            "price": price,
            "executed_at": now,
        }

    def get_trades(self, limit: int | None = None) -> list[dict]:
        sql = (
            "SELECT id, ticker, side, quantity, price, executed_at FROM trades ORDER BY id DESC"
        )
        if limit is not None:
            return self._query(sql + " LIMIT ?", (limit,))
        return self._query(sql)

    # -- snapshots -------------------------------------------------------
    def add_snapshot(self, total_value: float) -> None:
        with self._tx() as c:
            c.execute(
                "INSERT INTO portfolio_snapshots (total_value, recorded_at) VALUES (?, ?)",
                (total_value, _now()),
            )
            c.execute(
                "DELETE FROM portfolio_snapshots WHERE id <= "
                "(SELECT MAX(id) FROM portfolio_snapshots) - ?",
                (SNAPSHOT_RETENTION,),
            )

    def get_snapshots(self) -> list[dict]:
        return self._query(
            "SELECT total_value, recorded_at FROM portfolio_snapshots ORDER BY id"
        )

    # -- chat ------------------------------------------------------------
    def add_chat_message(self, role: str, content: str, actions=None) -> int:
        with self._tx() as c:
            cur = c.execute(
                "INSERT INTO chat_messages (role, content, actions, created_at) "
                "VALUES (?, ?, ?, ?)",
                (role, content, None if actions is None else json.dumps(actions), _now()),
            )
            return cur.lastrowid

    def get_chat_messages(self, limit: int = 20) -> list[dict]:
        rows = self._query(
            "SELECT id, role, content, actions, created_at FROM chat_messages "
            "ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        for r in rows:
            r["actions"] = json.loads(r["actions"]) if r["actions"] else None
        return rows[::-1]
