"""Shared business logic used by the REST routes and the chat flow."""

from __future__ import annotations

import math
from datetime import UTC, datetime

from app.storage import StorageError
from app.market import (
    InvalidTicker,
    MarketDataSource,
    PriceCache,
    PriceUnavailable,
    UnknownTicker,
    normalize_ticker,
    require_known,
    require_price,
)

MIN_QUANTITY = 0.0001


class ServiceError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _market_error(e: Exception) -> ServiceError:
    if isinstance(e, InvalidTicker):
        return ServiceError("invalid_ticker", "Ticker must be 1-5 letters", 400)
    if isinstance(e, UnknownTicker):
        return ServiceError("unknown_ticker", str(e), 400)
    return ServiceError("price_unavailable", str(e), 503)


def validate_quantity(quantity) -> float:
    ok = isinstance(quantity, (int, float)) and not isinstance(quantity, bool)
    if ok:
        q = float(quantity)
        ok = math.isfinite(q) and q >= MIN_QUANTITY and abs(round(q, 4) - q) < 1e-9
    if not ok:
        raise ServiceError(
            "invalid_quantity",
            "Quantity must be a number >= 0.0001 with at most 4 decimal places",
        )
    return round(q, 4)


class Services:
    def __init__(self, db, price_cache: PriceCache, source: MarketDataSource):
        self.db = db
        self.cache = price_cache
        self.source = source

    def _now(self) -> str:
        return datetime.now(UTC).isoformat()

    async def _normalize(self, raw: str) -> str:
        try:
            return await require_known(self.source, raw)
        except (InvalidTicker, UnknownTicker, PriceUnavailable) as e:
            raise _market_error(e) from e

    async def sync_tracked(self) -> None:
        tracked = set(self.db.get_watchlist())
        tracked |= {p["ticker"] for p in self.db.get_positions()}
        await self.source.set_tickers(tracked)

    def get_portfolio(self) -> dict:
        cash = self.db.get_cash()
        positions = []
        invested = 0.0
        value = 0.0
        for p in self.db.get_positions():
            price = self.cache.get_price(p["ticker"])
            if price is None:
                price = p["avg_cost"]
            mv = p["quantity"] * price
            cost = p["quantity"] * p["avg_cost"]
            pnl = mv - cost
            invested += cost
            value += mv
            positions.append(
                {
                    "ticker": p["ticker"],
                    "quantity": p["quantity"],
                    "avg_cost": round(p["avg_cost"], 4),
                    "current_price": price,
                    "market_value": round(mv, 2),
                    "unrealized_pnl": round(pnl, 2),
                    "unrealized_pnl_pct": round(pnl / cost * 100, 2) if cost else 0.0,
                }
            )
        return {
            "cash_balance": round(cash, 2),
            "total_value": round(cash + value, 2),
            "total_unrealized_pnl": round(value - invested, 2),
            "positions": positions,
        }

    def _watch_entry(self, ticker: str) -> dict:
        u = self.cache.get(ticker)
        return {
            "ticker": ticker,
            "price": u.price if u else None,
            "baseline_price": u.baseline_price if u else None,
            "change_pct": round(u.daily_change_percent, 2) if u else None,
        }

    def get_watchlist(self) -> list[dict]:
        return [self._watch_entry(t) for t in self.db.get_watchlist()]

    def record_snapshot(self) -> None:
        self.db.add_snapshot(self.get_portfolio()["total_value"])

    async def execute_trade(self, ticker: str, side: str, quantity) -> dict:
        ticker = await self._normalize(ticker)
        if side not in ("buy", "sell"):
            raise ServiceError("invalid_side", "side must be 'buy' or 'sell'")
        qty = validate_quantity(quantity)
        try:
            price = require_price(self.cache, ticker).price
        except PriceUnavailable as e:
            raise _market_error(e) from e

        try:
            trade = self.db.apply_trade(ticker, side, qty, price)
        except StorageError as e:
            raise ServiceError(e.code, e.message) from e
        await self.sync_tracked()
        self.record_snapshot()
        return {
            "trade": {
                "ticker": ticker,
                "side": side,
                "quantity": qty,
                "price": price,
                "executed_at": trade.get("executed_at", self._now()),
            },
            "portfolio": self.get_portfolio(),
        }

    async def watchlist_add(self, ticker: str) -> dict:
        ticker = await self._normalize(ticker)
        try:
            self.db.add_watchlist(ticker)
        except StorageError as e:
            raise ServiceError(e.code, e.message, 409) from e
        await self.sync_tracked()
        return self._watch_entry(ticker)

    async def watchlist_remove(self, ticker: str) -> None:
        try:
            ticker = normalize_ticker(ticker)
        except InvalidTicker as e:
            raise _market_error(e) from e
        try:
            self.db.remove_watchlist(ticker)
        except StorageError as e:
            raise ServiceError(e.code, e.message, 404) from e
        await self.sync_tracked()

    async def reset(self) -> None:
        self.db.reset()
        await self.sync_tracked()
