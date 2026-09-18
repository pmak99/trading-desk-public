"""A long scan keeps its quotes batch-warmed, never older than PRICE_TTL_SECONDS.

A quote is cached 30 s on purpose: it becomes the next chain's spot, and the
straddle is priced and the strikes placed against it (memory_cache.py). Until
2026-09-14 ConcurrentScanner batch-quoted every ticker once, up front, so only
the tickers reached in the first 30 s used it; every later ticker paid one quote
call of its own. The provider now re-quotes a ticker's batch (one call per 100
tickers) when a scanned ticker finds its quote expired, for as long as the scan
runs, and the TTL is unchanged.

The scan is simulated through the real ConcurrentScanner and
CachedOptionsDataProvider on a fake clock. Every Tradier call costs 0.5 s: the
shared limiter's spacing when a scan saturates its 120 calls a minute. Each price
is the clock time it was quoted, so a chain can say how old its spot was.
"""

import math
from collections import Counter
from datetime import date
from threading import Lock
from unittest.mock import MagicMock

import pytest

from src.domain.errors import Ok
from src.domain.types import Money
from src.infrastructure.cache.memory_cache import (
    PRICE_TTL_SECONDS,
    CachedOptionsDataProvider,
    MemoryCache,
)
from src.utils.concurrent_scanner import ConcurrentScanner

CALL_SECONDS = 0.5
EXPIRIES = [date(2026, 10, 16), date(2026, 10, 23), date(2026, 11, 20)]
EVENT = date(2026, 10, 13)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


class CountingTradier:
    """TradierAPI's quote, batch-quote, chain and listing calls, counted.

    The lock is the limiter: a call's slot is CALL_SECONDS after the last, and, like
    the limiter's polling wait, it is not first come, first served. admit() takes a
    slot for the one call made next with admitted=True, which does not wait
    (protocols.RateLimitedOptionsProvider). Every call is stamped when it goes out."""

    def __init__(self, clock, unquoted=()):
        self.clock = clock
        self.unquoted = set(unquoted)   # symbols the batch endpoint returns no price for
        self.calls = Counter()
        self.spot_ages = []
        self._lock = Lock()
        self._count_lock = Lock()

    def admit(self):
        with self._lock:
            self.clock.now += CALL_SECONDS
        return True

    def _call(self, kind, admitted=False):
        if admitted:
            at = self.clock.now             # nothing to wait for: it goes out now
        else:
            with self._lock:
                self.clock.now += CALL_SECONDS
                at = self.clock.now
        with self._count_lock:
            self.calls[kind] += 1
        return at

    def get_stock_price(self, ticker, admitted=False):
        return Ok(Money(self._call("quote", admitted)))

    def get_stock_prices_batch(self, tickers, admitted=False):
        assert len(tickers) <= 100
        at = self._call("batch", admitted)
        return Ok({t: Money(at) for t in tickers if t not in self.unquoted})

    def get_option_chain(self, ticker, expiration, stock_price=None, admitted=False):
        if stock_price is None:
            stock_price = Money(self._call("quote"))
        at = self._call("chain", admitted)
        self.spot_ages.append(at - float(stock_price.amount))
        return Ok(MagicMock(stock_price=stock_price))

    def get_expirations(self, ticker):
        self._call("expirations")
        return Ok(list(EXPIRIES))

    def find_nearest_expiration(self, ticker, target_date):
        raise AssertionError("the simulated analysis reads listings with get_expirations")


def _analyze(container, ticker, earnings_date, expiration_date, **kwargs):
    """What a ticker's analysis asks the provider for: its listing and three chains
    (implied-move expiration, trading expiration, term-structure back month)."""
    provider = container.cached_options_provider
    provider.get_expirations(ticker)
    for expiry in EXPIRIES:
        provider.get_option_chain(ticker, expiry)
    return {"status": "SUCCESS"}


def simulate_scan(n_tickers, workers=1, unquoted=()):
    clock = FakeClock()
    tradier = CountingTradier(clock, unquoted)
    container = MagicMock()
    container.config.thresholds.min_dte = 3
    container.cached_options_provider = CachedOptionsDataProvider(
        tradier, MemoryCache(ttl_seconds=30, max_size=1000, clock=clock))
    tickers = [f"T{i:03d}" for i in range(n_tickers)]
    start = clock.now
    result = ConcurrentScanner(container, max_workers=workers).scan_tickers(
        tickers=tickers,
        earnings_lookup=dict.fromkeys(tickers, (EVENT, "AMC")),
        analyze_func=_analyze,
    )
    assert result.success_count == n_tickers
    return tradier, clock.now - start, container.cached_options_provider


@pytest.mark.parametrize("n_tickers", [100, 250])
def test_a_long_scan_pays_no_single_quote_calls(n_tickers):
    tradier, seconds, _ = simulate_scan(n_tickers)
    assert tradier.calls["quote"] == 0, dict(tradier.calls)
    # One batch per 100 tickers up front, then about one per quote lifetime.
    assert tradier.calls["batch"] <= math.ceil(n_tickers / 100) + math.ceil(seconds / PRICE_TTL_SECONDS)


def test_the_100_ticker_scan_costs_fewer_tradier_calls_than_before():
    """Measured on this simulation: 1 batch + 85 single quotes before the re-warm
    (486 calls), 7 batches after (407)."""
    tradier, _, _ = simulate_scan(100)
    assert (tradier.calls["batch"], tradier.calls["quote"]) == (7, 0)
    assert sum(tradier.calls.values()) == 407


def test_no_chain_is_priced_on_a_quote_older_than_its_ttl():
    """Measured when the chain request goes out. Until 2026-09-14 the quote was read
    before the chain's slot, and this run's worst was 30.5 s: the quote aged by the
    slot's wait. It is now read after the slot is taken (tests/unit/test_spot_age.py
    holds a slot 40 s). One worker only: with several, this fake's clock jumps with
    every call another thread makes, and a thread the interpreter pauses between
    any two statements sees seconds of calls go by that take microseconds of real
    time, so it cannot measure a spot's age."""
    tradier, _, _ = simulate_scan(100)
    assert max(tradier.spot_ages) <= PRICE_TTL_SECONDS


def test_a_ticker_the_batch_cannot_quote_is_quoted_alone_not_batched_again():
    """A symbol with no `last` in the batch response: its misses fetch it alone,
    so it cannot turn every miss into a 100-symbol call."""
    tradier, _, _ = simulate_scan(10, unquoted={"T003"})
    assert tradier.calls["quote"] == 1
    assert tradier.calls["batch"] == 1


def test_the_warm_set_ends_with_the_scan():
    """After the scan a quote miss is one quote call, as for any /analyze."""
    tradier, _, provider = simulate_scan(10)
    provider.cache.clear()
    before = tradier.calls.copy()
    provider.get_option_chain("T001", EXPIRIES[0])
    assert tradier.calls - before == Counter({"quote": 1, "chain": 1})
