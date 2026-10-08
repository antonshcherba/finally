"""Tests for PriceUpdate."""

from datetime import datetime

import pytest

from app.market.models import PriceUpdate


def _update(price, previous, baseline=100.0, ts=1_700_000_000.0):
    return PriceUpdate("AAPL", price, previous, baseline, ts)


class TestDirection:
    def test_up(self):
        u = _update(101.0, 100.0)
        assert u.direction == "up" and u.change == 1.0 and u.change_percent == 1.0

    def test_down(self):
        u = _update(99.0, 100.0)
        assert u.direction == "down" and u.change == -1.0 and u.change_percent == -1.0

    def test_flat(self):
        u = _update(100.0, 100.0)
        assert u.direction == "flat" and u.change == 0.0 and u.change_percent == 0.0

    def test_zero_previous_price_does_not_divide(self):
        assert _update(10.0, 0.0).change_percent == 0.0


class TestDailyChange:
    def test_measured_against_baseline_not_previous(self):
        u = _update(price=105.0, previous=104.0, baseline=100.0)
        assert u.daily_change_percent == 5.0
        assert u.change_percent != u.daily_change_percent

    def test_negative(self):
        assert _update(95.0, 95.0, baseline=100.0).daily_change_percent == -5.0

    def test_zero_baseline(self):
        assert _update(10.0, 10.0, baseline=0.0).daily_change_percent == 0.0


class TestSse:
    def test_payload_has_exactly_the_plan_keys(self):
        assert set(_update(101.0, 100.0).to_sse()) == {
            "ticker",
            "price",
            "previous_price",
            "baseline_price",
            "timestamp",
            "direction",
        }

    def test_values(self):
        payload = _update(101.0, 100.0, baseline=99.0).to_sse()
        assert payload["ticker"] == "AAPL"
        assert payload["price"] == 101.0
        assert payload["previous_price"] == 100.0
        assert payload["baseline_price"] == 99.0
        assert payload["direction"] == "up"

    def test_timestamp_is_utc_iso(self):
        ts = _update(1.0, 1.0, ts=1_700_000_000.0).to_sse()["timestamp"]
        parsed = datetime.fromisoformat(ts)
        assert parsed.utcoffset().total_seconds() == 0
        assert parsed.timestamp() == 1_700_000_000.0


def test_immutable():
    with pytest.raises(AttributeError):
        _update(1.0, 1.0).price = 2.0  # type: ignore[misc]
