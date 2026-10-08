"""Tests for GBMSimulator (pure math, synchronous)."""

import math

import numpy as np
import pytest

from app.market.errors import InvalidTicker, UnknownTicker
from app.market.seed_prices import SEED_PRICES, TICKER_PARAMS
from app.market.simulator import GBMSimulator

DEFAULT_TEN = list(SEED_PRICES)


def _log_returns(sim, tickers, n):
    rows = []
    for _ in range(n):
        before = dict(sim._prices)
        sim.step()
        rows.append([math.log(sim._prices[t] / before[t]) for t in tickers])
    return np.array(rows)


class TestStep:
    def test_returns_every_ticker_rounded_to_cents(self):
        out = GBMSimulator(DEFAULT_TEN, seed=1).step()
        assert set(out) == set(DEFAULT_TEN)
        assert all(round(p, 2) == p for p in out.values())

    def test_empty_simulator(self):
        assert GBMSimulator([]).step() == {}

    def test_first_prices_are_near_seed(self):
        out = GBMSimulator(DEFAULT_TEN, seed=1).step()
        for t, p in out.items():
            assert abs(p / SEED_PRICES[t] - 1) < 0.01

    def test_prices_stay_positive_and_finite(self):
        sim = GBMSimulator(DEFAULT_TEN, event_probability=0.01, seed=1)
        for _ in range(5_000):
            assert all(p > 0 and math.isfinite(p) for p in sim.step().values())

    def test_seeded_runs_are_deterministic(self):
        a, b = GBMSimulator(DEFAULT_TEN, seed=42), GBMSimulator(DEFAULT_TEN, seed=42)
        assert [a.step() for _ in range(50)] == [b.step() for _ in range(50)]

    def test_different_seeds_differ(self):
        a, b = GBMSimulator(["AAPL"], seed=1), GBMSimulator(["AAPL"], seed=2)
        assert [a.step() for _ in range(20)] != [b.step() for _ in range(20)]

    def test_does_not_touch_global_random_state(self):
        import random

        random.seed(5)
        expected = random.random()
        random.seed(5)
        sim = GBMSimulator(["AAPL"], event_probability=0.5, seed=1)
        for _ in range(10):
            sim.step()
        assert random.random() == expected


class TestStatistics:
    def test_log_return_mean_and_std_match_gbm(self):
        # Large dt so the signal is measurable; no shocks so it is pure GBM.
        dt, mu, sigma = 1e-4, TICKER_PARAMS["AAPL"]["mu"], TICKER_PARAMS["AAPL"]["sigma"]
        sim = GBMSimulator(["AAPL"], dt=dt, event_probability=0, seed=7)
        r = _log_returns(sim, ["AAPL"], 40_000).ravel()
        assert r.std() == pytest.approx(sigma * math.sqrt(dt), rel=0.03)
        expected_mean = (mu - 0.5 * sigma**2) * dt
        # Standard error of the mean is sigma*sqrt(dt)/sqrt(n) — assert within 4 SE.
        assert abs(r.mean() - expected_mean) < 4 * sigma * math.sqrt(dt) / math.sqrt(len(r))

    def test_default_per_tick_volatility(self):
        sim = GBMSimulator(["AAPL"], event_probability=0, seed=7)
        std = _log_returns(sim, ["AAPL"], 20_000).std()
        assert std == pytest.approx(0.22 * math.sqrt(GBMSimulator.DEFAULT_DT), rel=0.05)

    def test_correlation_structure(self):
        sim = GBMSimulator(["AAPL", "MSFT", "JPM", "V", "TSLA"], event_probability=0, seed=7)
        r = np.corrcoef(_log_returns(sim, ["AAPL", "MSFT", "JPM", "V", "TSLA"], 20_000).T)
        assert abs(r[0, 1] - 0.6) < 0.04  # tech-tech
        assert abs(r[2, 3] - 0.5) < 0.04  # finance-finance
        assert abs(r[0, 2] - 0.3) < 0.04  # cross-sector
        assert abs(r[0, 4] - 0.3) < 0.04  # TSLA vs tech

    def test_events_move_prices_at_least_two_percent(self):
        sim = GBMSimulator(["AAPL"], event_probability=1.0, seed=3)
        prev = SEED_PRICES["AAPL"]
        for _ in range(200):
            price = sim.step()["AAPL"]
            assert abs(price / prev - 1) > 0.015
            prev = price

    def test_events_are_at_most_five_percent(self):
        sim = GBMSimulator(["AAPL"], event_probability=1.0, seed=3)
        prev = SEED_PRICES["AAPL"]
        for _ in range(200):
            price = sim.step()["AAPL"]
            assert abs(price / prev - 1) < 0.052
            prev = price

    def test_no_events_means_tiny_moves(self):
        sim = GBMSimulator(DEFAULT_TEN, event_probability=0, seed=3)
        for _ in range(1_000):
            before = dict(sim._prices)
            sim.step()
            assert all(abs(sim._prices[t] / before[t] - 1) < 0.005 for t in DEFAULT_TEN)


class TestTickerSet:
    def test_default_ten_factor(self):
        assert GBMSimulator(DEFAULT_TEN)._cholesky.shape == (10, 10)

    def test_single_ticker_has_no_cholesky(self):
        assert GBMSimulator(["AAPL"])._cholesky is None

    def test_correlation_matrix_is_positive_definite(self):
        sim = GBMSimulator(DEFAULT_TEN)
        corr = sim._cholesky @ sim._cholesky.T
        assert np.linalg.eigvalsh(corr).min() > 0.3
        assert np.allclose(np.diag(corr), 1.0)

    def test_add_ticker_is_priced_at_seed(self):
        sim = GBMSimulator(["AAPL"])
        sim.add_ticker("TSLA")
        assert sim.get_price("TSLA") == SEED_PRICES["TSLA"]
        assert sim._cholesky.shape == (2, 2)

    def test_add_duplicate_is_noop(self):
        sim = GBMSimulator(["AAPL"])
        sim.add_ticker("aapl")
        assert sim.get_tickers() == ["AAPL"]

    def test_remove_ticker(self):
        sim = GBMSimulator(["AAPL", "MSFT"])
        sim.remove_ticker("msft")
        assert sim.get_tickers() == ["AAPL"] and sim.get_price("MSFT") is None
        assert sim._cholesky is None
        assert "MSFT" not in sim.step()

    def test_remove_missing_is_noop(self):
        sim = GBMSimulator(["AAPL"])
        sim.remove_ticker("ZZZZ")
        assert sim.get_tickers() == ["AAPL"]

    def test_readd_resets_to_seed(self):
        sim = GBMSimulator(["AAPL"], event_probability=1.0, seed=1)
        for _ in range(10):
            sim.step()
        sim.remove_ticker("AAPL")
        sim.add_ticker("AAPL")
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]

    def test_tickers_are_normalized(self):
        sim = GBMSimulator([" aapl "])
        assert sim.get_tickers() == ["AAPL"]
        assert sim.get_price("aapl") == SEED_PRICES["AAPL"]

    def test_duplicates_in_constructor_collapse(self):
        assert GBMSimulator(["AAPL", "aapl", "AAPL"]).get_tickers() == ["AAPL"]

    def test_insertion_order_preserved(self):
        assert GBMSimulator(["V", "AAPL", "JPM"]).get_tickers() == ["V", "AAPL", "JPM"]


class TestKnownSet:
    def test_unknown_ticker_rejected_in_constructor(self):
        with pytest.raises(UnknownTicker):
            GBMSimulator(["AAPL", "ZZZZ"])

    def test_unknown_ticker_rejected_in_add(self):
        sim = GBMSimulator(["AAPL"])
        with pytest.raises(UnknownTicker):
            sim.add_ticker("ZZZZ")
        assert sim.get_tickers() == ["AAPL"]

    def test_malformed_ticker_rejected(self):
        with pytest.raises(InvalidTicker):
            GBMSimulator([]).add_ticker("not valid")

    def test_is_known(self):
        assert GBMSimulator.is_known("AAPL") and GBMSimulator.is_known(" nflx ")
        assert not GBMSimulator.is_known("ZZZZ")

    def test_all_defaults_are_known(self):
        assert all(GBMSimulator.is_known(t) for t in DEFAULT_TEN)

    def test_baseline_is_seed_price_and_constant(self):
        sim = GBMSimulator(["AAPL"], event_probability=1.0, seed=1)
        for _ in range(20):
            sim.step()
        assert sim.get_baseline("AAPL") == 190.0
        assert sim.get_baseline("zzzz") is None

    def test_every_seed_has_params(self):
        assert set(SEED_PRICES) == set(TICKER_PARAMS)
