"""Tests for ticker normalization and boundary helpers."""

import pytest

from app.market.cache import PriceCache
from app.market.errors import InvalidTicker, PriceUnavailable, UnknownTicker
from app.market.simulator import SimulatorDataSource
from app.market.tickers import normalize_ticker, require_known, require_price


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("AAPL", "AAPL"), (" aapl ", "AAPL"), ("v", "V"), ("Googl", "GOOGL")],
)
def test_normalize(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "TOOLONG", "AA1", "BRK.B", "A B", "123", "A-B"])
def test_normalize_rejects_bad_format(raw):
    with pytest.raises(InvalidTicker):
        normalize_ticker(raw)


def test_normalize_rejects_non_string():
    with pytest.raises(InvalidTicker):
        normalize_ticker(None)  # type: ignore[arg-type]


async def test_require_known_accepts_supported():
    assert await require_known(SimulatorDataSource(PriceCache()), " msft ") == "MSFT"


async def test_require_known_rejects_unknown():
    with pytest.raises(UnknownTicker):
        await require_known(SimulatorDataSource(PriceCache()), "ZZZZ")


async def test_require_known_rejects_invalid_before_asking_source():
    with pytest.raises(InvalidTicker):
        await require_known(SimulatorDataSource(PriceCache()), "not a ticker")


def test_require_price():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    assert require_price(cache, "AAPL").price == 190.0
    with pytest.raises(PriceUnavailable):
        require_price(cache, "MSFT")
