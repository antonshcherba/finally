"""Tests for SimulatorDataSource (async wrapper around GBMSimulator)."""

import asyncio

import pytest

from app.market.cache import PriceCache
from app.market.errors import InvalidTicker, UnknownTicker
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import SimulatorDataSource


@pytest.fixture
def cache():
    return PriceCache()


async def _started(cache, tickers, **kw):
    source = SimulatorDataSource(cache, update_interval=0.01, seed=3, **kw)
    await source.start(tickers)
    return source


async def test_start_seeds_cache_before_first_tick(cache):
    source = SimulatorDataSource(cache, update_interval=60, seed=1)
    await source.start(["AAPL", "googl"])
    try:
        assert set(cache.get_all()) == {"AAPL", "GOOGL"}
        assert cache.get_price("AAPL") == SEED_PRICES["AAPL"]
    finally:
        await source.stop()


async def test_baseline_is_seed_price(cache):
    source = await _started(cache, ["AAPL"], event_probability=1.0)
    try:
        await asyncio.sleep(0.1)
        update = cache.get("AAPL")
        assert update.baseline_price == SEED_PRICES["AAPL"]
        assert update.price != update.baseline_price
    finally:
        await source.stop()


async def test_prices_advance(cache):
    source = await _started(cache, ["AAPL", "MSFT"])
    try:
        v = cache.version
        await asyncio.sleep(0.1)
        assert cache.version > v + 2
    finally:
        await source.stop()


async def test_add_ticker_is_priced_immediately(cache):
    source = SimulatorDataSource(cache, update_interval=60)
    await source.start(["AAPL"])
    try:
        await source.add_ticker(" tsla ")
        assert cache.get_price("TSLA") == SEED_PRICES["TSLA"]
        assert source.get_tickers() == ["AAPL", "TSLA"]
    finally:
        await source.stop()


async def test_add_existing_does_not_clobber_cache_entry(cache):
    source = await _started(cache, ["AAPL"])
    try:
        await asyncio.sleep(0.05)
        before = cache.get("AAPL")
        await source.add_ticker("AAPL")
        assert cache.get("AAPL").timestamp >= before.timestamp
        assert source.get_tickers() == ["AAPL"]
    finally:
        await source.stop()


async def test_add_unknown_ticker_raises(cache):
    source = SimulatorDataSource(cache, update_interval=60)
    await source.start(["AAPL"])
    try:
        with pytest.raises(UnknownTicker):
            await source.add_ticker("ZZZZ")
        with pytest.raises(InvalidTicker):
            await source.add_ticker("bad ticker")
        assert source.get_tickers() == ["AAPL"] and "ZZZZ" not in cache
    finally:
        await source.stop()


async def test_start_skips_unsupported_tickers(cache):
    source = SimulatorDataSource(cache, update_interval=60)
    await source.start(["AAPL", "ZZZZ", "bad ticker"])
    try:
        assert source.get_tickers() == ["AAPL"]
    finally:
        await source.stop()


async def test_remove_evicts_and_is_not_resurrected(cache):
    source = await _started(cache, ["AAPL", "TSLA"])
    try:
        await source.remove_ticker("tsla")
        await asyncio.sleep(0.06)
        assert "TSLA" not in cache and source.get_tickers() == ["AAPL"]
    finally:
        await source.stop()


async def test_remove_missing_is_noop(cache):
    source = SimulatorDataSource(cache, update_interval=60)
    await source.start(["AAPL"])
    try:
        await source.remove_ticker("ZZZZ")
        assert source.get_tickers() == ["AAPL"]
    finally:
        await source.stop()


async def test_validate_ticker(cache):
    source = SimulatorDataSource(cache)
    assert await source.validate_ticker("AAPL") is True
    assert await source.validate_ticker("zzzz") is False


async def test_add_before_start_is_kept(cache):
    source = SimulatorDataSource(cache, update_interval=60)
    await source.add_ticker("NVDA")
    await source.start(["AAPL"])
    try:
        assert set(source.get_tickers()) == {"NVDA", "AAPL"}
    finally:
        await source.stop()


async def test_empty_start_then_add(cache):
    source = await _started(cache, [])
    try:
        await asyncio.sleep(0.03)
        assert len(cache) == 0
        await source.add_ticker("V")
        assert "V" in cache
    finally:
        await source.stop()


async def test_stop_is_idempotent_and_halts_writes(cache):
    source = await _started(cache, ["AAPL"])
    await source.stop()
    await source.stop()
    v = cache.version
    await asyncio.sleep(0.05)
    assert cache.version == v


async def test_stop_without_start(cache):
    await SimulatorDataSource(cache).stop()


async def test_loop_survives_a_failing_step(cache):
    source = await _started(cache, ["AAPL"])
    try:
        real_step = source._sim.step
        calls = {"n": 0}

        def flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return real_step()

        source._sim.step = flaky
        v = cache.version
        await asyncio.sleep(0.1)
        assert calls["n"] > 1 and cache.version > v
    finally:
        await source.stop()


def test_mode():
    assert SimulatorDataSource(PriceCache()).mode == "simulator"
