"""Interface conformance and set_tickers behaviour, across both sources."""

import pytest

from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


@pytest.mark.parametrize("cls", [SimulatorDataSource, MassiveDataSource])
def test_implements_interface(cls):
    assert issubclass(cls, MarketDataSource)
    assert not cls.__abstractmethods__
    assert cls.mode in {"simulator", "massive"}


def test_interface_is_abstract():
    with pytest.raises(TypeError):
        MarketDataSource()  # type: ignore[abstract]


def test_interface_declares_the_contract():
    assert MarketDataSource.__abstractmethods__ == {
        "start",
        "stop",
        "add_ticker",
        "remove_ticker",
        "get_tickers",
        "validate_ticker",
    }


async def test_set_tickers_adds_and_removes():
    cache = PriceCache()
    source = SimulatorDataSource(cache, update_interval=60)
    await source.start(["AAPL", "GOOGL"])
    try:
        await source.set_tickers(["aapl", "TSLA"])
        assert sorted(source.get_tickers()) == ["AAPL", "TSLA"]
        assert "GOOGL" not in cache and "TSLA" in cache
    finally:
        await source.stop()


async def test_set_tickers_is_idempotent():
    cache = PriceCache()
    source = SimulatorDataSource(cache, update_interval=60)
    await source.start(["AAPL"])
    try:
        await source.set_tickers(["AAPL", "MSFT"])
        v = cache.version
        await source.set_tickers(["MSFT", "AAPL"])
        assert cache.version == v
    finally:
        await source.stop()


async def test_set_tickers_skips_unknown_and_invalid_without_failing():
    source = SimulatorDataSource(PriceCache(), update_interval=60)
    await source.start(["AAPL"])
    try:
        await source.set_tickers(["AAPL", "ZZZZ", "bad ticker", "MSFT"])
        assert sorted(source.get_tickers()) == ["AAPL", "MSFT"]
    finally:
        await source.stop()


async def test_set_tickers_to_empty():
    cache = PriceCache()
    source = SimulatorDataSource(cache, update_interval=60)
    await source.start(["AAPL", "MSFT"])
    try:
        await source.set_tickers([])
        assert source.get_tickers() == [] and len(cache) == 0
    finally:
        await source.stop()


async def test_position_only_ticker_stays_tracked():
    """The caller passes watchlist ∪ positions, so a held ticker survives watchlist removal."""
    cache = PriceCache()
    source = SimulatorDataSource(cache, update_interval=60)
    watchlist, positions = {"AAPL", "TSLA"}, {"TSLA"}
    await source.start(sorted(watchlist | positions))
    try:
        watchlist.discard("TSLA")
        await source.set_tickers(watchlist | positions)
        assert "TSLA" in source.get_tickers() and cache.get_price("TSLA") is not None
        positions.discard("TSLA")  # sold out
        await source.set_tickers(watchlist | positions)
        assert "TSLA" not in source.get_tickers() and "TSLA" not in cache
    finally:
        await source.stop()
