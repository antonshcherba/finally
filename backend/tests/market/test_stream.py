"""Tests for the SSE endpoint. The generator is driven directly with a fake request:
httpx's ASGI transport buffers whole responses, so it hangs on an infinite stream."""

import asyncio
import json
from datetime import datetime

from fastapi import FastAPI

from app.market.cache import PriceCache
from app.market.stream import create_stream_router, format_sse, generate_price_events


class FakeRequest:
    client = None

    def __init__(self, polls: int):
        self._left = polls

    async def is_disconnected(self) -> bool:
        self._left -= 1
        return self._left < 0


async def collect(cache, polls, interval=0.001, heartbeat=15.0):
    return [
        f
        async for f in generate_price_events(
            cache, FakeRequest(polls), interval=interval, heartbeat=heartbeat
        )
    ]


def payloads(frames):
    return [json.loads(f[len("data: ") :]) for f in frames if f.startswith("data: ")]


async def test_starts_with_retry_directive():
    assert (await collect(PriceCache(), 1))[0] == "retry: 1000\n\n"


async def test_one_event_per_ticker_per_tick():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.update("MSFT", 420.0)
    events = payloads(await collect(cache, 1))
    assert sorted(e["ticker"] for e in events) == ["AAPL", "MSFT"]


async def test_event_matches_plan_payload():
    cache = PriceCache()
    cache.update("AAPL", 190.0, baseline_price=189.0)
    cache.update("AAPL", 191.0)
    (event,) = payloads(await collect(cache, 1))
    assert set(event) == {
        "ticker",
        "price",
        "previous_price",
        "baseline_price",
        "timestamp",
        "direction",
    }
    assert (event["price"], event["previous_price"], event["baseline_price"]) == (
        191.0,
        190.0,
        189.0,
    )
    assert event["direction"] == "up"
    datetime.fromisoformat(event["timestamp"])


async def test_every_tick_resends_all_tickers_even_if_unchanged():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    events = payloads(await collect(cache, 3))
    assert len(events) == 3


async def test_new_prices_appear_in_later_ticks():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    frames = []
    gen = generate_price_events(cache, FakeRequest(3), interval=0.001)
    async for f in gen:
        frames.append(f)
        if len(payloads(frames)) == 1:
            cache.update("AAPL", 195.0)
    assert [e["price"] for e in payloads(frames)] == [190.0, 195.0, 195.0]


async def test_removed_ticker_stops_streaming():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.update("MSFT", 420.0)
    frames = []
    async for f in generate_price_events(cache, FakeRequest(3), interval=0.001):
        frames.append(f)
        if len(payloads(frames)) == 2:
            cache.remove("MSFT")
    tickers = [e["ticker"] for e in payloads(frames)]
    assert tickers.count("AAPL") == 3 and tickers.count("MSFT") == 1


async def test_empty_cache_sends_heartbeat_not_data():
    frames = await collect(PriceCache(), 10, interval=0.01, heartbeat=0.03)
    assert ": heartbeat\n\n" in frames and payloads(frames) == []


async def test_no_heartbeat_while_data_flows():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    frames = await collect(cache, 10, interval=0.01, heartbeat=0.03)
    assert ": heartbeat\n\n" not in frames


async def test_stops_when_client_disconnects():
    assert len(await collect(PriceCache(), 0)) == 1  # just the retry line


async def test_cancellation_propagates_and_cleans_up():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    gen = generate_price_events(cache, FakeRequest(10_000), interval=0.01)

    async def consume():
        async for _ in gen:
            pass

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.cancelled()


def test_format_sse_is_compact_json_frame():
    assert format_sse({"a": 1, "b": [1, 2]}) == 'data: {"a":1,"b":[1,2]}\n\n'


def test_router_factory_is_reentrant():
    a, b = create_stream_router(PriceCache()), create_stream_router(PriceCache())
    assert a is not b and len(a.routes) == 1 and len(b.routes) == 1


def test_route_registered_with_event_stream_endpoint():
    app = FastAPI()
    app.include_router(create_stream_router(PriceCache()))
    app.include_router(create_stream_router(PriceCache()), prefix="/other")
    paths = [r.path for r in app.routes]
    assert paths.count("/api/stream/prices") == 1
    assert "/other/api/stream/prices" in paths
