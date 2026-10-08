"""Tests for the Massive client. Snapshots are built with the real massive models,
never MagicMock, so a wrong attribute name fails here instead of in production."""

import asyncio
import time
from types import SimpleNamespace

import pytest
from massive.exceptions import BadResponse
from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.errors import MarketDataAuthError, PriceUnavailable
from app.market.massive_client import (
    MAX_BACKOFF_SECONDS,
    MassiveDataSource,
    _is_auth_error,
    _to_unix_seconds,
    parse_snapshot,
)

NS_2023 = 1_700_000_000_000_000_000  # 2023-11-14 in nanoseconds


def snap(ticker="AAPL", price=191.1, prev_close=189.8, ts=NS_2023, **extra):
    raw = {"ticker": ticker, "updated": ts}
    if price is not None:
        raw["lastTrade"] = {"p": price, "s": 100, "t": ts, "x": 4}
    if prev_close is not None:
        raw["prevDay"] = {"o": 1, "h": 2, "l": 1, "c": prev_close, "v": 10}
    raw.update(extra)
    return TickerSnapshot.from_dict(raw)


def make_source(tickers, fetch, interval=60.0):
    cache = PriceCache()
    source = MassiveDataSource("test-key", cache, poll_interval=interval)
    source._client = object()  # satisfies the "started" guard
    source._tickers = list(tickers)
    source._fetch_snapshots = fetch
    return source, cache


class TestTimestampUnits:
    def test_nanoseconds(self):
        assert _to_unix_seconds(NS_2023) == pytest.approx(1_700_000_000.0)

    def test_microseconds(self):
        assert _to_unix_seconds(1_700_000_000_123_456) == pytest.approx(1_700_000_000.123456)

    def test_milliseconds(self):
        assert _to_unix_seconds(1_700_000_000_123) == pytest.approx(1_700_000_000.123)

    def test_seconds(self):
        assert _to_unix_seconds(1_700_000_000) == 1_700_000_000

    @pytest.mark.parametrize("raw", [None, 0, -5, "abc"])
    def test_unusable(self, raw):
        assert _to_unix_seconds(raw) is None


class TestParseSnapshot:
    def test_full_snapshot(self):
        p = parse_snapshot(snap())
        assert (p.ticker, p.price, p.baseline_price) == ("AAPL", 191.1, 189.8)
        assert p.timestamp == pytest.approx(1_700_000_000.0)

    def test_lowercase_ticker_is_normalized(self):
        assert parse_snapshot(snap(ticker="aapl")).ticker == "AAPL"

    def test_no_last_trade_falls_back_to_minute_bar(self):
        s = snap(price=None, min={"o": 1, "h": 1, "l": 1, "c": 192.0, "v": 1})
        assert parse_snapshot(s).price == 192.0

    def test_falls_back_to_day_close(self):
        s = snap(price=None, day={"o": 1, "h": 1, "l": 1, "c": 193.0, "v": 1})
        assert parse_snapshot(s).price == 193.0

    def test_falls_back_to_prev_day_close(self):
        p = parse_snapshot(snap(price=None, prev_close=50.0))
        assert p.price == 50.0 and p.baseline_price == 50.0

    def test_zero_prices_are_skipped(self):
        s = snap(price=0, day={"o": 0, "h": 0, "l": 0, "c": 0, "v": 0})
        assert parse_snapshot(s).price == 189.8

    def test_nothing_usable_returns_none(self):
        assert parse_snapshot(snap(price=None, prev_close=None)) is None

    def test_missing_prev_day_derives_baseline_from_todays_change(self):
        p = parse_snapshot(snap(price=110.0, prev_close=None, todaysChange=10.0))
        assert p.baseline_price == 100.0

    def test_no_baseline_available(self):
        assert parse_snapshot(snap(price=110.0, prev_close=None)).baseline_price is None

    def test_timestamp_falls_back_to_updated(self):
        raw = {"ticker": "AAPL", "updated": NS_2023, "lastTrade": {"p": 10.0}}
        p = parse_snapshot(TickerSnapshot.from_dict(raw))
        assert p.timestamp == pytest.approx(1_700_000_000.0)

    def test_timestamp_none_when_absent(self):
        p = parse_snapshot(TickerSnapshot.from_dict({"ticker": "X", "lastTrade": {"p": 10.0}}))
        assert p.timestamp is None

    def test_missing_ticker(self):
        assert parse_snapshot(SimpleNamespace(last_trade=SimpleNamespace(price=1.0))) is None
        assert parse_snapshot(SimpleNamespace(ticker="", last_trade=SimpleNamespace(price=1.0))) is None

    def test_garbage_values_do_not_raise(self):
        assert parse_snapshot(SimpleNamespace(ticker="X", last_trade=SimpleNamespace(price="abc"))) is None
        assert parse_snapshot(SimpleNamespace(ticker="X", last_trade=SimpleNamespace(price=True))) is None

    def test_wrong_attribute_name_is_not_silently_accepted(self):
        # The pre-fix code read `last_trade.timestamp`; the real model has no such field.
        assert not hasattr(snap().last_trade, "timestamp")
        assert snap().last_trade.sip_timestamp == NS_2023


class TestAuthDetection:
    @pytest.mark.parametrize(
        "body",
        [
            '{"status":"NOT_AUTHORIZED","message":"You are not entitled to this data."}',
            "Unknown API Key",
            "403 Forbidden",
            "401 Unauthorized",
        ],
    )
    def test_auth_errors(self, body):
        assert _is_auth_error(BadResponse(body))

    @pytest.mark.parametrize("body", ['{"status":"ERROR","message":"Too many requests"}', "timeout"])
    def test_other_errors(self, body):
        assert not _is_auth_error(BadResponse(body))


class TestPolling:
    async def test_poll_updates_cache(self):
        source, cache = make_source(
            ["AAPL", "GOOGL"], lambda t: [snap("AAPL", 190.5), snap("GOOGL", 175.25, 170.0)]
        )
        await source._poll_once()
        assert cache.get_price("AAPL") == 190.5 and cache.get_price("GOOGL") == 175.25
        assert cache.get("AAPL").baseline_price == 189.8
        assert cache.get("GOOGL").baseline_price == 170.0

    async def test_single_call_for_all_tickers(self):
        calls = []

        def fetch(tickers):
            calls.append(list(tickers))
            return []

        source, _ = make_source(["AAPL", "MSFT", "TSLA"], fetch)
        await source._poll_once()
        assert calls == [["AAPL", "MSFT", "TSLA"]]

    async def test_previous_price_tracks_across_polls(self):
        prices = iter([100.0, 101.0])
        source, cache = make_source(["AAPL"], lambda t: [snap("AAPL", next(prices), 99.0)])
        await source._poll_once()
        await source._poll_once()
        u = cache.get("AAPL")
        assert (u.price, u.previous_price, u.direction) == (101.0, 100.0, "up")

    async def test_ticker_missing_from_response_gets_no_price(self):
        source, cache = make_source(["AAPL", "NOPE"], lambda t: [snap("AAPL")])
        await source._poll_once()
        assert "AAPL" in cache and "NOPE" not in cache

    async def test_unusable_snapshot_is_skipped(self):
        bad = snap("BAD", price=None, prev_close=None)
        source, cache = make_source(["AAPL", "BAD"], lambda t: [bad, snap("AAPL")])
        await source._poll_once()
        assert "AAPL" in cache and "BAD" not in cache

    async def test_untracked_ticker_in_response_is_ignored(self):
        source, cache = make_source(["AAPL"], lambda t: [snap("AAPL"), snap("MSFT")])
        await source._poll_once()
        assert "MSFT" not in cache

    async def test_empty_ticker_list_makes_no_call(self):
        def fetch(_):
            raise AssertionError("should not be called")

        source, _ = make_source([], fetch)
        await source._poll_once()

    async def test_not_started_makes_no_call(self):
        source, _ = make_source(["AAPL"], lambda t: [snap()])
        source._client = None
        await source._poll_once()
        assert len(source._cache) == 0

    async def test_none_response(self):
        source, cache = make_source(["AAPL"], lambda t: None)
        await source._poll_once()
        assert len(cache) == 0

    async def test_removed_mid_request_is_not_resurrected(self):
        def slow_fetch(tickers):
            time.sleep(0.1)  # runs in a worker thread
            return [snap(t, 100.0) for t in tickers]

        source, cache = make_source(["AAPL", "MSFT"], slow_fetch)
        poll = asyncio.create_task(source._poll_once())
        await asyncio.sleep(0.03)
        await source.remove_ticker("MSFT")
        await poll
        assert "AAPL" in cache and "MSFT" not in cache

    async def test_worker_thread_gets_a_copy_of_the_ticker_list(self):
        seen = []

        def fetch(tickers):
            time.sleep(0.05)
            seen.append(list(tickers))
            return []

        source, _ = make_source(["AAPL"], fetch)
        poll = asyncio.create_task(source._poll_once())
        await asyncio.sleep(0.01)
        source._tickers.append("MSFT")
        await poll
        assert seen == [["AAPL"]]


class TestBackoff:
    async def test_failures_back_off_and_keep_last_prices(self):
        def boom(_):
            raise RuntimeError("429")

        source, cache = make_source(["AAPL"], boom, interval=60)
        cache.update("AAPL", 100.0)
        assert source._next_delay() == 60
        await source._poll_once()
        assert source._next_delay() == 120.0
        await source._poll_once()
        assert source._next_delay() == MAX_BACKOFF_SECONDS
        assert cache.get_price("AAPL") == 100.0

    async def test_backoff_doubles_below_the_cap(self):
        def boom(_):
            raise RuntimeError("x")

        source, _ = make_source(["AAPL"], boom, interval=5)
        delays = []
        for _ in range(3):
            await source._poll_once()
            delays.append(source._next_delay())
        assert delays == [10, 20, 40]

    async def test_cap_never_below_interval(self):
        source, _ = make_source(["AAPL"], lambda t: [], interval=300)
        source._consecutive_failures = 5
        assert source._next_delay() == 300

    async def test_success_resets_backoff(self):
        calls = {"n": 0}

        def flaky(_):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("blip")
            return [snap("AAPL", 101.0)]

        source, cache = make_source(["AAPL"], flaky, interval=30)
        await source._poll_once()
        assert source._next_delay() == 60
        await source._poll_once()
        assert source._next_delay() == 30 and cache.get_price("AAPL") == 101.0

    async def test_mid_run_auth_error_does_not_raise(self):
        def denied(_):
            raise BadResponse('{"status":"NOT_AUTHORIZED"}')

        source, _ = make_source(["AAPL"], denied)
        await source._poll_once()  # key revoked mid-run: log and back off, keep running
        assert source._consecutive_failures == 1


class TestStart:
    async def test_start_polls_once_before_returning(self, monkeypatch):
        monkeypatch.setattr("app.market.massive_client.RESTClient", lambda api_key: object())
        cache = PriceCache()
        source = MassiveDataSource("k", cache, poll_interval=60)
        source._fetch_snapshots = lambda t: [snap(x) for x in t]
        await source.start(["aapl", "MSFT", "AAPL"])
        try:
            assert source.get_tickers() == ["AAPL", "MSFT"]
            assert set(cache.get_all()) == {"AAPL", "MSFT"}
        finally:
            await source.stop()

    async def test_start_with_rejected_plan_raises_auth_error(self, monkeypatch):
        monkeypatch.setattr("app.market.massive_client.RESTClient", lambda api_key: object())
        source = MassiveDataSource("k", PriceCache(), poll_interval=60)

        def denied(_):
            raise BadResponse('{"status":"NOT_AUTHORIZED","message":"not entitled"}')

        source._fetch_snapshots = denied
        with pytest.raises(MarketDataAuthError):
            await source.start(["AAPL"])
        await source.stop()

    async def test_start_with_transient_error_still_starts(self, monkeypatch):
        monkeypatch.setattr("app.market.massive_client.RESTClient", lambda api_key: object())
        source = MassiveDataSource("k", PriceCache(), poll_interval=60)

        def boom(_):
            raise RuntimeError("network down")

        source._fetch_snapshots = boom
        await source.start(["AAPL"])
        try:
            assert source._task is not None and not source._task.done()
        finally:
            await source.stop()

    async def test_stop_is_idempotent(self, monkeypatch):
        monkeypatch.setattr("app.market.massive_client.RESTClient", lambda api_key: object())
        source = MassiveDataSource("k", PriceCache(), poll_interval=60)
        source._fetch_snapshots = lambda t: []
        await source.start(["AAPL"])
        await source.stop()
        await source.stop()
        assert source._client is None and source._task is None

    async def test_poll_loop_polls_again(self, monkeypatch):
        monkeypatch.setattr("app.market.massive_client.RESTClient", lambda api_key: object())
        cache = PriceCache()
        source = MassiveDataSource("k", cache, poll_interval=0.02)
        counter = iter(range(100, 200))
        source._fetch_snapshots = lambda t: [snap("AAPL", float(next(counter)))]
        await source.start(["AAPL"])
        try:
            await asyncio.sleep(0.15)
            assert cache.get_price("AAPL") > 101
        finally:
            await source.stop()


class TestTickers:
    async def test_add_normalizes_and_dedupes(self):
        source, _ = make_source(["AAPL"], lambda t: [])
        source._client = None  # no out-of-cycle poll
        await source.add_ticker(" msft ")
        await source.add_ticker("MSFT")
        assert source.get_tickers() == ["AAPL", "MSFT"]

    async def test_add_triggers_out_of_cycle_poll(self):
        source, cache = make_source(["AAPL"], lambda t: [snap(x, 50.0) for x in t])
        await source.add_ticker("MSFT")
        await asyncio.gather(*source._extra_polls)
        assert cache.get_price("MSFT") == 50.0

    async def test_add_existing_does_not_poll(self):
        source, _ = make_source(["AAPL"], lambda t: [])
        await source.add_ticker("AAPL")
        assert not source._extra_polls

    async def test_remove_evicts_from_cache(self):
        source, cache = make_source(["AAPL", "MSFT"], lambda t: [])
        cache.update("MSFT", 1.0)
        await source.remove_ticker("msft")
        assert source.get_tickers() == ["AAPL"] and "MSFT" not in cache

    async def test_remove_missing_is_noop(self):
        source, _ = make_source(["AAPL"], lambda t: [])
        await source.remove_ticker("ZZZZ")
        assert source.get_tickers() == ["AAPL"]

    async def test_stop_cancels_pending_extra_polls(self):
        def slow(_):
            time.sleep(0.2)
            return []

        source, _ = make_source(["AAPL"], slow)
        await source.add_ticker("MSFT")
        await source.stop()
        assert not source._extra_polls


class TestValidateTicker:
    async def test_priced_tracked_ticker_needs_no_call(self):
        def fetch(_):
            raise AssertionError("should not be called")

        source, cache = make_source(["AAPL"], fetch)
        cache.update("AAPL", 190.0)
        assert await source.validate_ticker("aapl") is True

    async def test_valid_symbol_returns_true_and_is_memoised(self):
        calls = []

        def fetch(tickers):
            calls.append(tickers)
            return [snap("NFLX", 600.0)]

        source, _ = make_source([], fetch)
        assert await source.validate_ticker("NFLX") is True
        assert await source.validate_ticker("NFLX") is True
        assert calls == [["NFLX"]]

    async def test_symbol_with_no_data_is_unknown(self):
        source, _ = make_source([], lambda t: [])
        assert await source.validate_ticker("ZZZZ") is False

    async def test_negative_result_is_not_cached(self):
        responses = iter([[], [snap("NEWCO", 12.0)]])
        source, _ = make_source([], lambda t: next(responses))
        assert await source.validate_ticker("NEWCO") is False
        assert await source.validate_ticker("NEWCO") is True

    async def test_snapshot_for_another_symbol_does_not_validate(self):
        source, _ = make_source([], lambda t: [snap("AAPL")])
        assert await source.validate_ticker("MSFT") is False

    async def test_transient_error_is_price_unavailable_not_unknown(self):
        def boom(_):
            raise RuntimeError("timeout")

        source, _ = make_source([], boom)
        with pytest.raises(PriceUnavailable):
            await source.validate_ticker("AAPL")

    async def test_auth_error_surfaces(self):
        def denied(_):
            raise BadResponse('{"status":"NOT_AUTHORIZED"}')

        source, _ = make_source([], denied)
        with pytest.raises(MarketDataAuthError):
            await source.validate_ticker("AAPL")

    async def test_not_started(self):
        source = MassiveDataSource("k", PriceCache())
        with pytest.raises(PriceUnavailable):
            await source.validate_ticker("AAPL")


def test_mode():
    assert MassiveDataSource("k", PriceCache()).mode == "massive"
