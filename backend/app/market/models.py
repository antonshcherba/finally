"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time.

    `baseline_price` is the simulator's seed price, or Massive's previous close.
    Daily change % is measured against it; direction is measured against
    `previous_price` (the prior tick/poll) and drives the UI flash.
    """

    ticker: str
    price: float
    previous_price: float
    baseline_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds

    @property
    def change(self) -> float:
        """Absolute price change from the previous update."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percentage change from the previous update."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down', or 'flat' relative to the previous update."""
        if self.price > self.previous_price:
            return "up"
        elif self.price < self.previous_price:
            return "down"
        return "flat"

    @property
    def daily_change_percent(self) -> float:
        """Percentage change from the baseline: the watchlist's change %."""
        if not self.baseline_price:
            return 0.0
        return round((self.price - self.baseline_price) / self.baseline_price * 100, 4)

    def to_sse(self) -> dict:
        """The PLAN.md §6 SSE payload."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "baseline_price": self.baseline_price,
            "timestamp": datetime.fromtimestamp(self.timestamp, UTC).isoformat(),
            "direction": self.direction,
        }
