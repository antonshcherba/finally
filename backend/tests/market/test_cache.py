"""Tests for PriceCache."""

import threading

from app.market.cache import PriceCache


class TestUpdate:
    def test_first_update_is_flat_and_baseline_is_price(self):
        u = PriceCache().update("AAPL", 190.0)
        assert u.previous_price == 190.0 and u.baseline_price == 190.0
        assert u.direction == "flat"

    def test_second_update_tracks_previous(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        u = c.update("AAPL", 191.0)
        assert u.previous_price == 190.0 and u.direction == "up"

    def test_baseline_is_sticky_when_omitted(self):
        c = PriceCache()
        c.update("AAPL", 190.0, baseline_price=185.0)
        assert c.update("AAPL", 191.0).baseline_price == 185.0

    def test_baseline_can_be_replaced(self):
        c = PriceCache()
        c.update("AAPL", 190.0, baseline_price=185.0)
        assert c.update("AAPL", 191.0, baseline_price=186.0).baseline_price == 186.0

    def test_rounds_to_cents(self):
        assert PriceCache().update("AAPL", 190.12345).price == 190.12

    def test_int_price_becomes_float(self):
        assert isinstance(PriceCache().update("AAPL", 190).price, float)

    def test_zero_timestamp_is_respected(self):
        assert PriceCache().update("AAPL", 1.0, timestamp=0.0).timestamp == 0.0

    def test_default_timestamp_is_now(self):
        assert PriceCache().update("AAPL", 1.0).timestamp > 1_600_000_000


class TestReads:
    def test_get_unknown(self):
        c = PriceCache()
        assert c.get("NOPE") is None and c.get_price("NOPE") is None

    def test_get_and_get_price(self):
        c = PriceCache()
        c.update("AAPL", 190.5)
        assert c.get("AAPL").price == 190.5 and c.get_price("AAPL") == 190.5

    def test_get_all_is_a_copy(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        snapshot = c.get_all()
        snapshot.clear()
        assert "AAPL" in c

    def test_snapshot_pairs_version_and_prices(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        version, prices = c.snapshot()
        assert version == c.version and set(prices) == {"AAPL"}

    def test_len_and_contains(self):
        c = PriceCache()
        assert len(c) == 0 and "AAPL" not in c
        c.update("AAPL", 190.0)
        assert len(c) == 1 and "AAPL" in c


class TestVersionAndRemove:
    def test_version_bumps_on_each_update(self):
        c = PriceCache()
        v0 = c.version
        c.update("AAPL", 1.0)
        c.update("AAPL", 1.0)
        assert c.version == v0 + 2

    def test_remove_bumps_version_only_when_present(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        v = c.version
        c.remove("AAPL")
        c.remove("AAPL")
        assert c.version == v + 1 and "AAPL" not in c

    def test_readd_after_remove_is_fresh(self):
        c = PriceCache()
        c.update("AAPL", 190.0)
        c.remove("AAPL")
        assert c.update("AAPL", 150.0).previous_price == 150.0


def test_concurrent_writers_do_not_lose_updates():
    c = PriceCache()

    def write(ticker):
        for i in range(500):
            c.update(ticker, 100.0 + i)

    threads = [threading.Thread(target=write, args=(f"T{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.version == 2000 and len(c) == 4
