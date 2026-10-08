"""Tests for the source factory."""

import pytest

from app.market.cache import PriceCache
from app.market.errors import MarketDataAuthError
from app.market.factory import (
    DEFAULT_MASSIVE_POLL_INTERVAL,
    create_market_data_source,
    start_market_data,
)
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_POLL_INTERVAL", raising=False)


class TestCreate:
    def test_no_key_gives_simulator(self):
        assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)

    @pytest.mark.parametrize("value", ["", "   ", "\t\n"])
    def test_blank_key_gives_simulator(self, monkeypatch, value):
        monkeypatch.setenv("MASSIVE_API_KEY", value)
        assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)

    def test_key_gives_massive(self, monkeypatch):
        monkeypatch.setenv("MASSIVE_API_KEY", "  secret  ")
        source = create_market_data_source(PriceCache())
        assert isinstance(source, MassiveDataSource)
        assert source._api_key == "secret"

    def test_source_is_not_started(self):
        assert create_market_data_source(PriceCache()).get_tickers() == []

    def test_cache_is_wired_through(self, monkeypatch):
        cache = PriceCache()
        assert create_market_data_source(cache)._cache is cache
        monkeypatch.setenv("MASSIVE_API_KEY", "k")
        assert create_market_data_source(cache)._cache is cache


class TestPollInterval:
    def _interval(self, monkeypatch, value=None):
        monkeypatch.setenv("MASSIVE_API_KEY", "k")
        if value is not None:
            monkeypatch.setenv("MASSIVE_POLL_INTERVAL", value)
        return create_market_data_source(PriceCache())._interval

    def test_default(self, monkeypatch):
        assert self._interval(monkeypatch) == DEFAULT_MASSIVE_POLL_INTERVAL == 15.0

    def test_custom(self, monkeypatch):
        assert self._interval(monkeypatch, "5") == 5.0

    def test_float(self, monkeypatch):
        assert self._interval(monkeypatch, "2.5") == 2.5

    def test_floor_of_one_second(self, monkeypatch):
        assert self._interval(monkeypatch, "0.01") == 1.0

    @pytest.mark.parametrize("value", ["abc", "", "  "])
    def test_invalid_falls_back_to_default(self, monkeypatch, value):
        assert self._interval(monkeypatch, value) == DEFAULT_MASSIVE_POLL_INTERVAL


class TestStartMarketData:
    async def test_simulator_without_key(self):
        cache = PriceCache()
        source = await start_market_data(cache, ["AAPL", "MSFT"])
        try:
            assert source.mode == "simulator"
            assert set(cache.get_all()) == {"AAPL", "MSFT"}
        finally:
            await source.stop()

    async def test_rejected_key_falls_back_to_simulator(self, monkeypatch):
        monkeypatch.setenv("MASSIVE_API_KEY", "free-tier-key")

        async def denied(self, tickers):
            raise MarketDataAuthError("NOT_AUTHORIZED")

        monkeypatch.setattr(MassiveDataSource, "start", denied)
        cache = PriceCache()
        source = await start_market_data(cache, ["AAPL"])
        try:
            assert source.mode == "simulator"
            assert cache.get_price("AAPL") is not None
        finally:
            await source.stop()

    async def test_accepted_key_uses_massive(self, monkeypatch):
        monkeypatch.setenv("MASSIVE_API_KEY", "paid-key")
        started = []

        async def ok(self, tickers):
            started.append(list(tickers))

        monkeypatch.setattr(MassiveDataSource, "start", ok)
        source = await start_market_data(PriceCache(), ["AAPL"])
        assert source.mode == "massive" and started == [["AAPL"]]

    async def test_non_auth_errors_propagate(self, monkeypatch):
        monkeypatch.setenv("MASSIVE_API_KEY", "k")

        async def boom(self, tickers):
            raise RuntimeError("unexpected")

        monkeypatch.setattr(MassiveDataSource, "start", boom)
        with pytest.raises(RuntimeError):
            await start_market_data(PriceCache(), ["AAPL"])
