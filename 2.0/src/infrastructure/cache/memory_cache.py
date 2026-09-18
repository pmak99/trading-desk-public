"""
In-memory cache with per-entry TTL and LRU eviction, and the Tradier wrapper
that fronts every scan, /analyze and bot fetch with it.

⚠️ Until 2026-09-14 `get` measured every entry against the cache-wide
`ttl_seconds` (the container's CACHE_L1_TTL, 30 s) and `set` only logged the TTL
it was handed, so CachedOptionsDataProvider's 60 s chains and 1 h expirations
lived 30 s, and the scan's price pre-warm asked for 300 s, which never took
effect. Eviction, documented as LRU, dropped the oldest insert however recently
it was read.
"""

import logging
import time
from collections import OrderedDict
from contextlib import contextmanager
from datetime import date
from threading import Lock
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, cast

from src.domain.errors import AppError, Err, ErrorCode, Ok
from src.domain.protocols import (
    CacheProvider,
    OptionsDataProvider,
    RateLimitedOptionsProvider,
)

logger = logging.getLogger(__name__)

# Cache version for key namespacing
# Increment this to invalidate all caches after schema/format changes
CACHE_VERSION = "v1"

# Per-entry TTLs CachedOptionsDataProvider stores with (seconds).
#
# Quotes: 30 s, what every quote actually lived before the TTL fix. A cached
# quote is injected into the next chain fetch as that chain's stock_price, and
# the chain's stock_price prices the straddle at spot
# (ImpliedMoveCalculatorInterpolated) and places the ATM and OTM strikes
# (liquidity, skew, spread strikes) against option quotes fetched fresh. This
# TTL is the most the spot can lag the option prices it is read with (read after
# the chain's rate-limit slot is taken, _priced_chain); a longer one would move
# the implied move and the strikes, not just their age.
PRICE_TTL_SECONDS = 30
# Quotes one chain may buy while waiting for its rate-limit slot before it is
# refused RATELIMIT: each is bought only when the one before aged past its TTL in
# that wait, which takes a limiter saturated for the whole of it (_priced_chain).
SPOT_QUOTES_PER_CHAIN = 3
# Times one chain is sent again after a send that got no usable answer -- a 429,
# a server error, no answer in time, no connection (AppError.retryable): the
# retries TradierAPI's session gave it until 2026-09-14 (429) and 2026-09-15 (the
# rest), when it resent on the old spot. One bound for every kind; each resend
# takes its own slot and reads the quote after it (_priced_chain).
RESENDS_PER_CHAIN = 3
# Chains: 60 s. A cached chain ages as one snapshot (its spot still within 30 s
# of its quotes), and everything price-sensitive reads spot and quotes from the
# same chain. 60 s lets a ticker's implied move, liquidity, skew and term
# structure share one fetch while a saturated rate limiter spaces the calls.
CHAIN_TTL_SECONDS = 60
# Expirations: 1 h. A listing changes when a new series is listed, not within
# a session.
EXPIRATIONS_TTL_SECONDS = 3600


class MemoryCache:
    """
    Thread-safe in-memory cache: each entry expires on its own TTL, and a full
    cache evicts the least recently used entry.
    """

    def __init__(
        self,
        ttl_seconds: int = 30,
        max_size: int = 1000,
        clock: Callable[[], float] = time.monotonic,
    ):
        """
        Initialize memory cache.

        Args:
            ttl_seconds: TTL for an entry `set` without its own
            max_size: Maximum number of entries (LRU eviction)
            clock: Seconds on a monotonic clock; tests pass a fake one
        """
        self.ttl_seconds = ttl_seconds
        self.max_size = max_size
        self._clock = clock
        # key -> (value, expires_at); ordered least to most recently used
        self._entries: "OrderedDict[str, Tuple[Any, float]]" = OrderedDict()
        self._lock = Lock()  # Thread safety for all cache operations

    def get(self, key: str) -> Optional[Any]:
        """
        Get cached value by key.

        Args:
            key: Cache key

        Returns:
            Cached value or None if expired/missing
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                logger.debug(f"Cache MISS: {key}")
                return None

            value, expires_at = entry
            if self._clock() > expires_at:
                logger.debug(f"Cache EXPIRED: {key}")
                del self._entries[key]
                return None

            self._entries.move_to_end(key)
            logger.debug(f"Cache HIT: {key}")
            return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        """
        Set cached value with optional custom TTL.

        Args:
            key: Cache key
            value: Value to cache
            ttl: Seconds this entry lives; the cache-wide ttl_seconds if None
        """
        effective_ttl = ttl if ttl is not None else self.ttl_seconds
        with self._lock:
            if key in self._entries:
                self._entries.move_to_end(key)
            else:
                while self._entries and len(self._entries) >= self.max_size:
                    evicted, _ = self._entries.popitem(last=False)
                    logger.debug(f"Cache EVICTED: {evicted} (max size reached)")
            self._entries[key] = (value, self._clock() + effective_ttl)
            logger.debug(f"Cache SET: {key} (TTL: {effective_ttl}s)")

    def delete(self, key: str) -> None:
        """Delete cached value."""
        with self._lock:
            if self._entries.pop(key, None) is not None:
                logger.debug(f"Cache DELETE: {key}")

    def clear(self) -> None:
        """Clear all cached values."""
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            logger.info(f"Cache CLEARED: {count} entries removed")

    def size(self) -> int:
        """Get current cache size (expired entries count until read or evicted)."""
        with self._lock:
            return len(self._entries)

    def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        with self._lock:
            size = len(self._entries)
            return {
                "size": size,
                "max_size": self.max_size,
                "ttl_seconds": self.ttl_seconds,
                "utilization_pct": (size / self.max_size * 100)
                if self.max_size > 0
                else 0,
            }


class CachedOptionsDataProvider:
    """
    Wrapper that adds caching to any OptionsDataProvider, and is one itself
    (src/domain/protocols.py: CachedOptionsProvider).

    Usage:
        provider = TradierAPI(api_key)
        cached_provider = CachedOptionsDataProvider(provider, cache)
    """

    def __init__(self, provider: OptionsDataProvider, cache: CacheProvider):
        self.provider = provider
        self.cache = cache
        # The scan's warm set (keep_stock_prices_warm): its batch-call groups, and
        # each ticker's group. Empty outside a scan.
        self._warm_batches: List[List[str]] = []
        self._warm_batch_of: Dict[str, int] = {}
        self._warm_lock = Lock()

    def _cached_price(self, ticker_normalized: str):
        """The cached quote, re-warming the ticker's batch when a scan's expired."""
        key = f"{CACHE_VERSION}:stock_price:{ticker_normalized}"
        cached = self.cache.get(key)
        if cached is None:
            self._rewarm(ticker_normalized)
            cached = self.cache.get(key)    # this re-warm's, or another worker's
        return cached

    def _rewarm(self, ticker_normalized: str, admitted: bool = False) -> bool:
        """Re-quote the warm batch holding this ticker when it is due; True if this
        call made the batch call.

        One batch call refreshes up to 100 tickers, where the misses it answers
        would each cost a quote call. A batch quoted within PRICE_TTL_SECONDS is
        not quoted again: a ticker still missing from it (no `last` in the
        response, or a failed call) is fetched alone. admitted: spend a slot the
        caller took with the provider's admit().
        """
        if ticker_normalized not in self._warm_batch_of:
            return False
        # A caller holding a slot never waits for another worker's re-warm, which
        # may itself be waiting for the limiter: the slot would sit unspent.
        if not self._warm_lock.acquire(blocking=not admitted):
            return False
        try:
            index = self._warm_batch_of.get(ticker_normalized)
            if index is None:       # the scan ended while this worker waited
                return False
            # Another worker may have re-warmed it while this one waited.
            if self.cache.get(f"{CACHE_VERSION}:stock_price:{ticker_normalized}") is not None:
                return False
            if self.cache.get(f"{CACHE_VERSION}:price_warm:{index}") is not None:
                return False
            self._warm_batch(self._warm_batches[index], index, admitted)
        finally:
            self._warm_lock.release()
        return True

    def _warm_batch(self, batch: List[str], index: int, admitted: bool = False) -> int:
        """One batch quote call; seeds the price cache. Returns how many it cached."""
        # Marked even on failure, so an outage costs one batch call per TTL, not one per miss.
        self.cache.set(f"{CACHE_VERSION}:price_warm:{index}", True, ttl=PRICE_TTL_SECONDS)
        # Reached only for a provider keep_stock_prices_warm found batch quotes on
        # (TradierAPI); OptionsDataProvider itself does not name the call.
        provider = cast(RateLimitedOptionsProvider, self.provider)
        result = (provider.get_stock_prices_batch(batch, admitted=True) if admitted
                  else provider.get_stock_prices_batch(batch))
        if result.is_err:
            logger.warning(f"Price pre-warm batch failed: {result.error}")
            return 0
        for ticker, price in result.value.items():
            key = f"{CACHE_VERSION}:stock_price:{ticker.upper()}"
            self.cache.set(key, Ok(price), ttl=PRICE_TTL_SECONDS)
        return len(result.value)

    def get_stock_price(self, ticker: str):
        """Get stock price with caching."""
        # Normalize ticker and use versioned cache key
        ticker_normalized = ticker.upper()
        key = f"{CACHE_VERSION}:stock_price:{ticker_normalized}"
        cached = self._cached_price(ticker_normalized)

        if cached is not None:
            return cached

        result = self.provider.get_stock_price(ticker)
        if result.is_ok:
            self.cache.set(key, result, ttl=PRICE_TTL_SECONDS)

        return result

    def get_option_chain(self, ticker: str, expiration, stock_price=None):
        """Get option chain with caching.

        stock_price: the protocol's price hint. A caller's price is passed to the
        provider as given; without one, the chain is priced on a quote at most
        PRICE_TTL_SECONDS old when its request goes out (_priced_chain).
        """
        # Normalize ticker and use versioned cache key
        ticker_normalized = ticker.upper()
        key = f"{CACHE_VERSION}:option_chain:{ticker_normalized}:{expiration}"
        cached = self.cache.get(key)

        if cached is not None:
            return cached

        if stock_price is not None:
            result = self.provider.get_option_chain(ticker, expiration, stock_price=stock_price)
        else:
            result = self._priced_chain(ticker, ticker_normalized, expiration)
        if result.is_ok:
            self.cache.set(key, result, ttl=CHAIN_TTL_SECONDS)

        return result

    def _priced_chain(self, ticker: str, ticker_normalized: str, expiration):
        """The chain, priced on a quote no older than PRICE_TTL_SECONDS when the
        chain request goes out.

        The cached quote is injected so the provider skips its own price fetch:
        one rate-limit call saved per chain after a ticker's first. But a quote
        ages while the chain waits for the shared limiter, and that wait is bounded
        by the 120 s budget, not the TTL: the limiter is not first come, first
        served. Until 2026-09-14 the quote was read before the wait (and TradierAPI
        quoted before it when nothing was cached), so a chain whose slot came 40 s
        late went out on a 40 s old spot (tests/unit/test_spot_age.py). Behind a
        limiter the chain's slot is now taken first and the cache read after it:
        the cache's TTL is the freshness clock, and it starts when a quote is
        stored, so the bound is the TTL plus that quote's response time. With no
        fresh quote, the slot buys one and the chain waits for the next. A provider
        without a limiter sends the chain as soon as it is called.

        A chain send that gets no usable answer comes back retryable -- a 429 once
        the client has waited out the window, a server error or failed connection
        after a short wait, a timeout at once -- and goes round again: a new slot,
        the quote read after it (bought again if the failure aged it), then the
        chain -- at most RESENDS_PER_CHAIN more times, whatever the mix.
        """
        price_key = f"{CACHE_VERSION}:stock_price:{ticker_normalized}"
        if not isinstance(self.provider, RateLimitedOptionsProvider):
            cached_price = self._cached_price(ticker_normalized)
            if cached_price is not None and cached_price.is_ok:
                return self.provider.get_option_chain(ticker, expiration, stock_price=cached_price.value)
            result = self.provider.get_option_chain(ticker, expiration)
            if result.is_ok:
                # A fresh chain fetch also fetched a fresh price — cache it
                self.cache.set(price_key, Ok(result.value.stock_price), ttl=PRICE_TTL_SECONDS)
            return result

        quotes = resends = 0
        failed = None
        while quotes < SPOT_QUOTES_PER_CHAIN:
            # A due batch re-quote waits for its own slot, before the chain's.
            self._cached_price(ticker_normalized)
            if not self.provider.admit():
                return Err(AppError(ErrorCode.RATELIMIT, "Tradier rate limit exceeded"))
            quote = self.cache.get(price_key)
            if quote is None:
                # Aged out while this call waited: the slot buys the quote, in
                # the ticker's scan batch when that is due, and the chain waits
                # for the next slot.
                if self._rewarm(ticker_normalized, admitted=True):
                    quotes += 1
                    continue
                quote = self.cache.get(price_key)     # another worker's re-warm
                if quote is None:
                    quoted = self.provider.get_stock_price(ticker, admitted=True)
                    if quoted.is_err:
                        return Err(quoted.error)
                    self.cache.set(price_key, quoted, ttl=PRICE_TTL_SECONDS)
                    quotes += 1
                    continue
            result = self.provider.get_option_chain(
                ticker, expiration, stock_price=quote.value, admitted=True
            )
            if result.is_ok or not result.error.retryable or resends == RESENDS_PER_CHAIN:
                return result
            resends += 1
            failed = result.error
        last = f"; {resends} chain sends failed, the last {failed}" if failed else ""
        return Err(AppError(
            ErrorCode.RATELIMIT,
            f"{ticker}: {SPOT_QUOTES_PER_CHAIN} quotes each aged past {PRICE_TTL_SECONDS}s "
            f"before the chain's rate-limit slot came{last}",
        ))

    def get_expirations(self, ticker: str):
        """Get available option expirations with caching (listings change rarely)."""
        ticker_normalized = ticker.upper()
        key = f"{CACHE_VERSION}:expirations:{ticker_normalized}"
        cached = self.cache.get(key)

        if cached is not None:
            return cached

        result = self.provider.get_expirations(ticker)
        if result.is_ok:
            self.cache.set(key, result, ttl=EXPIRATIONS_TTL_SECONDS)

        return result

    @contextmanager
    def keep_stock_prices_warm(self, tickers: List[str]) -> Iterator[int]:
        """
        Batch-quote many tickers now, and again whenever one of them finds its
        quote expired, until the block exits. Yields how many were quoted now.

        One batch call covers up to 100 tickers for a single rate-limit token,
        versus one per ticker fetched individually. The quotes live
        PRICE_TTL_SECONDS like any other (a quote is the next chain's spot), so
        until 2026-09-14, when this warmed once at scan start, only the tickers
        reached in the first 30 s used it and every later one paid its own quote
        call: 85 of a simulated 100-ticker scan's (tests/unit/test_price_rewarm.py).
        Now the first miss after a batch expires re-quotes that batch.

        A provider without batch quotes yields 0 and changes nothing; a failed
        batch call is a warning, and those tickers are fetched individually.
        """
        if getattr(self.provider, 'get_stock_prices_batch', None) is None or not tickers:
            yield 0
            return

        unique = list(dict.fromkeys(t.upper() for t in tickers))
        batches = [unique[i:i + 100] for i in range(0, len(unique), 100)]
        batch_of = {t: i for i, batch in enumerate(batches) for t in batch}
        with self._warm_lock:
            self._warm_batches, self._warm_batch_of = batches, batch_of
            warmed = sum(self._warm_batch(batch, i) for i, batch in enumerate(batches))
        if warmed:
            logger.info(f"Pre-warmed {warmed}/{len(unique)} stock prices")
        try:
            yield warmed
        finally:
            with self._warm_lock:
                if self._warm_batch_of is batch_of:
                    self._warm_batches, self._warm_batch_of = [], {}

    def find_nearest_expiration(self, ticker: str, target_date: date):
        """Find the nearest available expiration >= target_date, via cached expirations."""
        expirations_result = self.get_expirations(ticker)
        if expirations_result.is_err:
            return Err(expirations_result.error)

        expirations = sorted(expirations_result.value)

        nearest = None
        for exp in expirations:
            if exp >= target_date:
                nearest = exp
                break

        if nearest is None:
            if expirations:
                nearest = expirations[-1]
                logger.warning(
                    f"{ticker}: No expiration >= {target_date}, using {nearest}"
                )
            else:
                return Err(
                    AppError(ErrorCode.NODATA, f"No expirations for {ticker}")
                )

        if nearest != target_date:
            logger.info(f"{ticker}: Adjusted expiration {target_date} → {nearest}")

        return Ok(nearest)
