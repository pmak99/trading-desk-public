"""MemoryCache honours each entry's TTL and evicts the least recently used entry.

Until 2026-09-14 `get` measured every entry against the cache-wide `ttl_seconds`
(the container's CACHE_L1_TTL, 30 s) and `set` only logged the TTL it was handed,
so CachedOptionsDataProvider's 60 s chains and 1 h expirations lived 30 s. The
documented LRU was FIFO: eviction dropped the oldest insert however recently it
was read, found by a linear scan of the timestamps.

Everything here runs on a fake clock: nothing sleeps.
"""

from datetime import date

from src.domain.errors import AppError, Err, ErrorCode, Ok  # noqa: E402
from src.domain.types import Money  # noqa: E402
from src.infrastructure.cache.memory_cache import (  # noqa: E402
    CHAIN_TTL_SECONDS,
    EXPIRATIONS_TTL_SECONDS,
    PRICE_TTL_SECONDS,
    CachedOptionsDataProvider,
    MemoryCache,
)


class FakeClock:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


# --- TTL --------------------------------------------------------------------

def test_an_entry_lives_for_its_own_ttl_not_the_cache_default():
    clock = FakeClock()
    cache = MemoryCache(ttl_seconds=30, clock=clock)

    cache.set("chain", "value", ttl=60)
    clock.advance(45)
    assert cache.get("chain") == "value"
    clock.advance(16)
    assert cache.get("chain") is None


def test_a_shorter_entry_ttl_expires_before_the_default():
    clock = FakeClock()
    cache = MemoryCache(ttl_seconds=30, clock=clock)

    cache.set("quote", "value", ttl=10)
    clock.advance(11)
    assert cache.get("quote") is None


def test_the_cache_wide_ttl_is_the_default():
    clock = FakeClock()
    cache = MemoryCache(ttl_seconds=30, clock=clock)

    cache.set("k", "value")
    clock.advance(30)
    assert cache.get("k") == "value", "an entry is still fresh at exactly its TTL"
    clock.advance(1)
    assert cache.get("k") is None


def test_setting_a_key_again_restarts_its_ttl():
    clock = FakeClock()
    cache = MemoryCache(ttl_seconds=30, clock=clock)

    cache.set("k", "old", ttl=10)
    clock.advance(8)
    cache.set("k", "new", ttl=10)
    clock.advance(8)
    assert cache.get("k") == "new"


def test_an_expired_entry_is_removed_when_read():
    clock = FakeClock()
    cache = MemoryCache(ttl_seconds=30, clock=clock)

    cache.set("k", "value", ttl=5)
    clock.advance(6)
    assert cache.get("k") is None
    assert cache.size() == 0


# --- LRU --------------------------------------------------------------------

def test_eviction_drops_the_least_recently_used_entry_not_the_oldest_insert():
    cache = MemoryCache(ttl_seconds=30, max_size=3, clock=FakeClock())
    for key in ("a", "b", "c"):
        cache.set(key, key)

    assert cache.get("a") == "a"          # a is now the most recently used
    cache.set("d", "d")

    assert cache.get("b") is None
    assert [cache.get(k) for k in ("a", "c", "d")] == ["a", "c", "d"]


def test_eviction_follows_the_full_use_order():
    cache = MemoryCache(ttl_seconds=30, max_size=3, clock=FakeClock())
    for key in ("a", "b", "c"):
        cache.set(key, key)
    cache.get("b")
    cache.get("a")                        # least recently used first: c, b, a

    cache.set("d", "d")                   # evicts c: b, a, d
    cache.set("e", "e")                   # evicts b: a, d, e
    assert cache.get("c") is None
    assert cache.get("b") is None
    assert [cache.get(k) for k in ("a", "d", "e")] == ["a", "d", "e"]


def test_overwriting_a_key_at_capacity_evicts_nothing_and_refreshes_its_use():
    cache = MemoryCache(ttl_seconds=30, max_size=2, clock=FakeClock())
    cache.set("a", 1)
    cache.set("b", 2)

    cache.set("a", 3)
    assert cache.size() == 2
    cache.set("c", 4)                     # b is least recently used now
    assert cache.get("b") is None
    assert cache.get("a") == 3


def test_delete_clear_and_stats():
    cache = MemoryCache(ttl_seconds=30, max_size=4, clock=FakeClock())
    cache.set("a", 1)
    cache.set("b", 2)

    cache.delete("a")
    cache.delete("missing")
    assert cache.get("a") is None
    assert cache.get_stats() == {"size": 1, "max_size": 4, "ttl_seconds": 30,
                                 "utilization_pct": 25.0}

    cache.clear()
    assert cache.size() == 0


# --- CachedOptionsDataProvider ---------------------------------------------

EXPIRY = date(2026, 10, 16)


class FakeTradier:
    """Counts calls the way Tradier bills them: a chain without a price costs two."""

    def __init__(self, chain):
        self.chain = chain
        self.calls = []

    def get_stock_price(self, ticker):
        self.calls.append(("quote", ticker))
        return Ok(Money(101.0))

    def get_stock_prices_batch(self, tickers):
        self.calls.append(("batch", tuple(tickers)))
        return Ok({t: Money(100.0) for t in tickers})

    def get_option_chain(self, ticker, expiration, stock_price=None):
        if stock_price is None:
            self.calls.append(("quote", ticker))
        self.calls.append(("chain", ticker, expiration))
        return Ok(self.chain)

    def get_expirations(self, ticker):
        self.calls.append(("expirations", ticker))
        return Ok([EXPIRY])


def _provider(chain, clock):
    tradier = FakeTradier(chain)
    # The container's cache: CACHE_L1_TTL (30 s) is only the default
    return tradier, CachedOptionsDataProvider(tradier, MemoryCache(ttl_seconds=30, clock=clock))


def test_the_ttls_are_the_ones_chosen():
    assert (PRICE_TTL_SECONDS, CHAIN_TTL_SECONDS, EXPIRATIONS_TTL_SECONDS) == (30, 60, 3600)


def test_a_chain_is_one_call_for_its_60_seconds(sample_option_chain):
    clock = FakeClock()
    tradier, provider = _provider(sample_option_chain, clock)

    provider.get_option_chain("AAPL", EXPIRY)
    clock.advance(59)
    provider.get_option_chain("aapl", EXPIRY)
    assert [c[0] for c in tradier.calls] == ["quote", "chain"]

    clock.advance(2)
    provider.get_option_chain("AAPL", EXPIRY)
    assert [c[0] for c in tradier.calls] == ["quote", "chain", "quote", "chain"]


def test_expirations_are_one_call_for_an_hour(sample_option_chain):
    clock = FakeClock()
    tradier, provider = _provider(sample_option_chain, clock)

    provider.get_expirations("AAPL")
    clock.advance(3599)
    provider.find_nearest_expiration("AAPL", EXPIRY)
    assert tradier.calls == [("expirations", "AAPL")]

    clock.advance(2)
    provider.get_expirations("AAPL")
    assert len(tradier.calls) == 2


def test_a_warmed_price_is_never_injected_into_a_chain_after_30_seconds(sample_option_chain):
    """The chain's stock_price prices the straddle at spot and picks strikes; a
    warm batch quote paired with option quotes fetched minutes later would skew
    both. The pre-warm asked for 300 s until 2026-09-14, which never took effect
    (30 s did). An expired warm quote is re-quoted in its batch while the scan
    runs (tests/unit/test_price_rewarm.py), and alone after it."""
    clock = FakeClock()
    tradier, provider = _provider(sample_option_chain, clock)

    with provider.keep_stock_prices_warm(["AAPL", "MSFT"]) as warmed:
        assert warmed == 2
        clock.advance(30)
        provider.get_option_chain("AAPL", EXPIRY)
        assert tradier.calls == [("batch", ("AAPL", "MSFT")), ("chain", "AAPL", EXPIRY)], \
            "a 30 s old quote is still fresh"

        clock.advance(1)
        provider.get_option_chain("MSFT", EXPIRY)
        assert tradier.calls[-2:] == [("batch", ("AAPL", "MSFT")), ("chain", "MSFT", EXPIRY)]

    clock.advance(31)
    provider.get_option_chain("AAPL", date(2026, 10, 23))
    assert tradier.calls[-2:] == [("quote", "AAPL"), ("chain", "AAPL", date(2026, 10, 23))]


def test_a_quote_fetched_with_a_chain_is_cached_for_30_seconds(sample_option_chain):
    clock = FakeClock()
    tradier, provider = _provider(sample_option_chain, clock)

    provider.get_option_chain("AAPL", EXPIRY)
    clock.advance(30)
    assert provider.get_stock_price("AAPL").value == sample_option_chain.stock_price
    clock.advance(1)
    provider.get_stock_price("AAPL")
    assert tradier.calls == [("quote", "AAPL"), ("chain", "AAPL", EXPIRY), ("quote", "AAPL")]


def test_a_failed_fetch_is_not_cached():
    class Down(FakeTradier):
        def get_expirations(self, ticker):
            self.calls.append(("expirations", ticker))
            return Err(AppError(ErrorCode.EXTERNAL, "down"))

    tradier = Down(None)
    provider = CachedOptionsDataProvider(tradier, MemoryCache(clock=FakeClock()))
    provider.get_expirations("AAPL")
    provider.get_expirations("AAPL")
    assert len(tradier.calls) == 2
