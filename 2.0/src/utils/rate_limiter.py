"""
Rate limiters for the vendor APIs.

Finnhub: a token bucket. Tradier: a sliding window its client owns. Yahoo
(yfinance) and Twelve Data: a VendorGate each -- a sliding window plus a shared
backoff -- one per process, reached through vendor_gate(). Twelve Data's gate
also counts the host's calls in the vendor's day (DailyCallCounter).
"""

import fcntl
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Callable, Dict, Optional, Protocol, TypeVar, Union

from src.config.config import RateLimitConfig

logger = logging.getLogger(__name__)

T = TypeVar("T")


class RateLimiter(Protocol):
    """What an API client needs from a limiter."""

    def wait_for_token(self, tokens: int = 1, timeout: Optional[float] = None) -> bool:
        ...


class TokenBucketRateLimiter:
    """
    Token bucket rate limiter.

    Tokens are added at a constant rate. Each request consumes a token.
    If no tokens available, request must wait or fail.

    Example:
        # Allow 5 requests per minute
        limiter = TokenBucketRateLimiter(rate=5, per_seconds=60)

        if limiter.acquire():
            make_api_call()
        else:
            print("Rate limit exceeded")
    """

    def __init__(
        self,
        rate: int,
        per_seconds: int,
        burst: Optional[int] = None,
    ):
        """
        Initialize rate limiter.

        Args:
            rate: Number of tokens to add per time window
            per_seconds: Time window in seconds
            burst: Maximum tokens to accumulate (defaults to rate)
        """
        self.rate = rate
        self.per_seconds = per_seconds
        self.burst = burst if burst is not None else rate

        # Token state
        self.tokens = float(self.burst)
        self.last_refill = time.time()
        self.lock = Lock()

        logger.debug(
            f"Rate limiter: {rate} requests per {per_seconds}s "
            f"(burst: {self.burst})"
        )

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self.last_refill

        # Calculate tokens to add
        tokens_to_add = (elapsed / self.per_seconds) * self.rate
        self.tokens = min(self.burst, self.tokens + tokens_to_add)
        self.last_refill = now

    def acquire(self, tokens: int = 1, blocking: bool = False) -> bool:
        """
        Attempt to acquire tokens.

        Args:
            tokens: Number of tokens to acquire
            blocking: If True, wait until tokens available

        Returns:
            True if tokens acquired, False if rate limit exceeded
        """
        with self.lock:
            self._refill()

            if self.tokens >= tokens:
                self.tokens -= tokens
                logger.debug(
                    f"Token acquired. Remaining: {self.tokens:.2f}/{self.burst}"
                )
                return True

            if not blocking:
                logger.warning(
                    f"Rate limit exceeded. Need {tokens}, have {self.tokens:.2f}"
                )
                return False

            # Calculate wait time inside lock (thread-safe)
            wait_time = self._calculate_wait_time(tokens)

        # Blocking mode: wait outside lock (don't hold lock while sleeping)
        logger.info(f"Rate limit: waiting {wait_time:.2f}s for token")
        time.sleep(wait_time)

        # Try again after waiting
        with self.lock:
            self._refill()
            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    def _calculate_wait_time(self, tokens: int) -> float:
        """Calculate time to wait for tokens to be available."""
        tokens_needed = tokens - self.tokens
        if tokens_needed <= 0:
            return 0.0

        time_per_token = self.per_seconds / self.rate
        return tokens_needed * time_per_token

    def wait_for_token(
        self, tokens: int = 1, timeout: Optional[float] = None
    ) -> bool:
        """
        Wait until tokens are available or timeout.

        Args:
            tokens: Number of tokens needed
            timeout: Maximum seconds to wait (None = wait forever)

        Returns:
            True if tokens acquired, False if timeout
        """
        start_time = time.time()

        while True:
            if self.acquire(tokens, blocking=False):
                return True

            if timeout is not None:
                elapsed = time.time() - start_time
                if elapsed >= timeout:
                    logger.warning(f"Rate limit wait timeout after {elapsed:.2f}s")
                    return False

            # Sleep a fraction of the refill time
            sleep_time = min(1.0, self.per_seconds / self.rate / 2)
            time.sleep(sleep_time)

    def get_tokens(self) -> float:
        """Get current token count (for monitoring)."""
        with self.lock:
            self._refill()
            return self.tokens

    def reset(self) -> None:
        """Reset limiter to full tokens (for testing)."""
        with self.lock:
            self.tokens = float(self.burst)
            self.last_refill = time.time()
        logger.debug("Rate limiter reset")


class CompositeRateLimiter:
    """
    Multiple rate limiters in series (e.g., per-minute AND per-day).

    All limiters must approve before allowing a request.

    Example:
        limiter = CompositeRateLimiter([
            TokenBucketRateLimiter(5, 60),    # 5/minute
            TokenBucketRateLimiter(500, 86400),  # 500/day
        ])
    """

    def __init__(self, limiters: list[TokenBucketRateLimiter]):
        self.limiters = limiters
        logger.debug(f"Composite rate limiter with {len(limiters)} limiters")

    def acquire(self, tokens: int = 1, blocking: bool = False) -> bool:
        """Acquire tokens from all limiters."""
        # Try all in non-blocking mode first
        if not blocking:
            return all(
                limiter.acquire(tokens, blocking=False)
                for limiter in self.limiters
            )

        # Blocking mode: acquire from each in sequence
        for limiter in self.limiters:
            if not limiter.acquire(tokens, blocking=True):
                return False
        return True

    def wait_for_token(
        self, tokens: int = 1, timeout: Optional[float] = None
    ) -> bool:
        """Wait for all limiters to have tokens available."""
        start_time = time.time()

        for limiter in self.limiters:
            if timeout is not None:
                elapsed = time.time() - start_time
                remaining_timeout = timeout - elapsed
                if remaining_timeout <= 0:
                    return False

                if not limiter.wait_for_token(tokens, remaining_timeout):
                    return False
            else:
                if not limiter.wait_for_token(tokens, None):
                    return False

        return True

    def reset(self) -> None:
        """Reset all limiters."""
        for limiter in self.limiters:
            limiter.reset()


class SlidingWindowRateLimiter:
    """
    At most `max_calls` in ANY `per_seconds` window.

    A token bucket cannot promise that: it admits its burst plus a full window of
    refill, so a 120/min bucket lets ~240 through a minute even with burst 120.
    A vendor that counts calls per window rejects the excess. This keeps the
    timestamps of the calls in the last window and admits a call only while
    there are fewer than `max_calls` of them -- bursts are still immediate, up
    to the limit.

    `clock` and `sleep` are injectable so the guarantee can be tested without
    waiting a minute.
    """

    def __init__(
        self,
        max_calls: int,
        per_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        self.max_calls = max_calls
        self.per_seconds = per_seconds
        self._clock = clock
        self._sleep = sleep
        self._calls: deque[float] = deque()
        self.lock = Lock()

    def _prune(self, now: float) -> None:
        while self._calls and self._calls[0] <= now - self.per_seconds:
            self._calls.popleft()

    def _try(self, tokens: int) -> float:
        """Take `tokens` slots and return 0, or return seconds until they free up."""
        with self.lock:
            now = self._clock()
            self._prune(now)
            if len(self._calls) + tokens <= self.max_calls:
                self._calls.extend([now] * tokens)
                return 0.0
            # The slot we need frees when the oldest call that must expire does.
            blocking = self._calls[len(self._calls) + tokens - self.max_calls - 1]
            return blocking + self.per_seconds - now

    def acquire(self, tokens: int = 1, blocking: bool = False) -> bool:
        if not blocking:
            return self._try(tokens) == 0.0
        return self.wait_for_token(tokens, timeout=None)

    def wait_for_token(self, tokens: int = 1, timeout: Optional[float] = None) -> bool:
        """Wait for a slot. Returns False, without sleeping out the timeout, as soon
        as the next slot is known to be further away than the time left."""
        if tokens > self.max_calls:
            return False
        start = self._clock()
        while True:
            wait = self._try(tokens)
            if wait == 0.0:
                return True
            if timeout is not None and (self._clock() - start) + wait > timeout:
                logger.warning(
                    f"Rate limit: next slot in {wait:.1f}s exceeds the {timeout:.0f}s wait budget"
                )
                return False
            self._sleep(wait)

    def get_tokens(self) -> float:
        """Slots free right now (for monitoring)."""
        with self.lock:
            self._prune(self._clock())
            return float(self.max_calls - len(self._calls))

    def reset(self) -> None:
        with self.lock:
            self._calls.clear()


def create_finnhub_limiter(per_minute: int = 60) -> TokenBucketRateLimiter:
    """
    Create rate limiter for Finnhub API.

    Finnhub free tier: 60 calls/minute. Tunable via FINNHUB_PER_MINUTE
    (config.rate_limits.finnhub_per_minute).
    """
    return TokenBucketRateLimiter(rate=per_minute, per_seconds=60, burst=per_minute)


def create_tradier_limiter(
    per_minute: int = 120,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> SlidingWindowRateLimiter:
    """
    Create rate limiter for Tradier API.

    Tradier market data allows 120 requests per minute and aggregates "over
    1-minute intervals starting with the first request" (docs.tradier.com/docs/
    rate-limiting). Tunable via TRADIER_PER_MINUTE
    (config.rate_limits.tradier_per_minute).

    ⚠️ Until 2026-09-13 this was a token bucket with burst 150 (125% of the
    rate), which admits 150 calls at once and ~270 in a minute; no burst value
    can make a bucket respect a per-window count. A sliding window can.
    """
    return SlidingWindowRateLimiter(per_minute, 60.0, clock=clock, sleep=sleep)


# --- Yahoo and Twelve Data: vendors with no client object of their own --------

class VendorRateLimited(Exception):
    """No call slot within the wait budget: the window stayed full, or the vendor
    paused this process for longer than the caller can wait."""


class VendorDailyLimit(VendorRateLimited):
    """This host's calls for the vendor's day reached the ceiling. Nothing frees a
    slot before the vendor's day turns, so the call was not sent."""


class DailyCallCounter:
    """Calls to one vendor in the vendor's day (UTC), counted across every process
    on the host: the bot, record_moves, a backfill or TACO run from a terminal.

    The count is a one-line file, "<UTC date> <count>". take() changes it under an
    exclusive flock -- read it, start again from 0 when the date has moved on,
    refuse at the ceiling, else write count + 1 -- so two callers can neither lose
    a count nor both take the last one. flock belongs to an open file description,
    so it separates threads that each open the file as well as processes, and the
    kernel drops it with the process that held it: a crash cannot wedge the next
    caller. The record is rewritten in place at a fixed width, never truncated, so
    no reader sees an empty file; one it cannot parse counts as 0.

    A file, not a Postgres row: a Twelve Data fetch should not fail because the
    database is unreachable, and a per-host count was not worth a migration. It
    counts one host; the Mac and the server share the key, so the ceiling leaves
    headroom and Twelve Data's own "run out of API credits for the day" still
    stops a caller on either.
    """

    _WIDTH = 10          # digits in the count field: the record is always 22 bytes

    def __init__(self, path: Union[str, Path], ceiling: int,
                 now: Callable[[], float] = time.time):
        if ceiling < 1:
            raise ValueError("ceiling must be at least 1")
        self.path = Path(path).expanduser()
        self.ceiling = ceiling
        self._now = now

    def day(self) -> str:
        """The vendor's day: today's date in UTC."""
        return datetime.fromtimestamp(self._now(), tz=timezone.utc).date().isoformat()

    def take(self) -> bool:
        """Count one call and return True, or return False at the ceiling."""
        day = self.day()
        fd = self._open()
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            count = self._read(fd, day)
            if count >= self.ceiling:
                return False
            os.pwrite(fd, f"{day} {count + 1:>{self._WIDTH}}\n".encode(), 0)
            return True
        finally:
            os.close(fd)                       # releases the lock

    def used(self) -> int:
        """Calls counted today."""
        day = self.day()
        fd = self._open()
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
            return self._read(fd, day)
        finally:
            os.close(fd)

    def _open(self) -> int:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        return os.open(self.path, os.O_RDWR | os.O_CREAT, 0o600)

    @staticmethod
    def _read(fd: int, day: str) -> int:
        fields = os.pread(fd, 64, 0).decode(errors="replace").split("\n", 1)[0].split()
        if len(fields) == 2 and fields[0] == day and fields[1].isdigit():
            return int(fields[1])
        return 0


class VendorGate:
    """Every call one process makes to one vendor.

    Tradier and Finnhub have client objects that own a limiter. Yahoo (yfinance)
    and Twelve Data are called from a dozen scripts, so the limit lives here and
    each call site wraps its request in `gate.call(fn, retry_after)`:

      - a SlidingWindowRateLimiter: at most `max_calls` in any window;
      - a backoff shared by every caller. When the vendor says slow down,
        `retry_after(error)` gives the wait (0 for exponential: `base_delay`
        doubling per consecutive refusal, up to `max_delay`) and the whole gate
        pauses -- the next caller waits it out instead of spending its own
        retries against a limit another call already hit. A success resets it.

      - optionally a DailyCallCounter: every request, retries included, takes
        one of the host's calls for the vendor's day first, and at the ceiling
        the call raises VendorDailyLimit without a request.

    One call makes at most `attempts` requests and waits at most `budget`
    seconds for slots and pauses together. When either runs out it raises the
    vendor's last error, or VendorRateLimited if it never got to ask -- the call
    site's existing failure path, never an open-ended wait. A pause longer than
    the budget therefore fails later calls at once, without a request, so a loop
    over a hundred tickers against a blocked vendor does not stall.
    """

    def __init__(
        self,
        name: str,
        limiter: SlidingWindowRateLimiter,
        *,
        attempts: int,
        base_delay: float,
        max_delay: float,
        budget: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        daily: Optional[DailyCallCounter] = None,
    ):
        if attempts < 1:
            raise ValueError("attempts must be at least 1")
        self.name = name
        self.limiter = limiter
        self.daily = daily
        self.attempts = attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.budget = budget
        self._clock = clock
        self._sleep = sleep
        self._lock = Lock()
        self._paused_until = float("-inf")
        self._strikes = 0

    def call(self, fn: Callable[[], T], retry_after: Callable[[Exception], Optional[float]]) -> T:
        """Run `fn` under the window, retrying the errors `retry_after` recognises.

        `retry_after(error)` returns None for an error that is not a rate limit
        (raised at once), else seconds to wait (the vendor's Retry-After), or 0
        to back off exponentially.
        """
        start = self._clock()
        last_error: Optional[Exception] = None
        for _ in range(self.attempts):
            if not self._wait_for_slot(start):
                if last_error is not None:
                    raise last_error
                raise VendorRateLimited(f"{self.name}: no call slot within {self.budget:g}s")
            if self.daily is not None and not self.daily.take():
                raise VendorDailyLimit(
                    f"{self.name}: this host's daily ceiling of {self.daily.ceiling} calls "
                    f"is spent for {self.daily.day()} UTC ({self.daily.path})")
            try:
                result = fn()
            except Exception as e:
                wait = retry_after(e)
                if wait is None:
                    raise
                self._pause(wait, e)
                last_error = e
                continue
            with self._lock:
                self._strikes = 0
            return result
        # attempts >= 1 (__init__), and every pass that falls through set last_error.
        assert last_error is not None
        raise last_error

    def _pause(self, wait: float, error: Exception) -> None:
        with self._lock:
            self._strikes += 1
            if wait <= 0:
                wait = self.base_delay * 2 ** min(self._strikes - 1, 16)
            wait = min(wait, self.max_delay)
            self._paused_until = max(self._paused_until, self._clock() + wait)
        logger.warning(f"{self.name}: {type(error).__name__} ({str(error)[:160]}); "
                       f"pausing calls {wait:.1f}s")

    def _wait_for_slot(self, start: float) -> bool:
        while True:
            with self._lock:
                pause = self._paused_until - self._clock()
            if pause <= 0:
                break
            if (self._clock() - start) + pause > self.budget:
                return False
            self._sleep(pause)
        left = self.budget - (self._clock() - start)
        return left >= 0 and self.limiter.wait_for_token(timeout=left)


def create_yahoo_gate(
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> VendorGate:
    """
    Yahoo Finance through yfinance: one call a second; on YFRateLimitError wait
    2 s, doubling to 60 s; a call gives up after 3 requests or 30 s.

    Yahoo publishes no limit and throttles by IP; yfinance turns its HTTP 429
    into YFRateLimitError, and one yfinance call is 1-3 requests (cookie, crumb,
    data). One a second is the spacing the two unattended Yahoo loops already
    ran at without a 429 (record_moves and resolve_ambiguous_moves, 1.0 s each),
    now held across every caller in the process. It costs a serial loop little
    -- a call takes 0.1-0.6 s on its own (measured 2026-09-14) -- and a scan
    little more, because Tradier's 120 a minute already holds a scan to well
    under a ticker a second.
    """
    return VendorGate(
        "Yahoo", SlidingWindowRateLimiter(1, 1.0, clock=clock, sleep=sleep),
        attempts=3, base_delay=2.0, max_delay=60.0, budget=30.0, clock=clock, sleep=sleep,
    )


# Read by record_moves' runtime test: its last fetch can outlast the job's own
# deadline by the wait budget plus one HTTP timeout per attempt.
TWELVE_DATA_ATTEMPTS = 3
TWELVE_DATA_WAIT_BUDGET_SEC = 150.0


def create_twelve_data_gate(
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    daily: Optional[DailyCallCounter] = None,
) -> VendorGate:
    """
    Twelve Data, Basic (free) plan: 8 API credits a minute, reset at the start
    of each minute, and 800 a day, reset at 00:00 UTC
    (support.twelvedata.com/en/articles/5194820 and /5615854-credits).

    At most 8 in any 60 s also holds in every calendar minute. The limit is per
    key, not per process -- record_moves, a backfill and the bot's auto-backfill
    can still collide -- and a retry that waits for the next minute absorbs that.
    Budget 150 s: a full window (60 s) plus one wait into the next minute (61 s).

    The day is counted for the whole host (DailyCallCounter): at
    TWELVE_DATA_DAILY_CEILING calls (default 780) in TWELVE_DATA_USAGE_FILE every
    process's next call is refused without a request until 00:00 UTC. Until
    2026-09-14 nothing counted the day but record_moves' --budget, and only its
    own calls; that stays the job's own, lower cap.
    """
    if daily is None:
        limits = RateLimitConfig.from_env()
        daily = DailyCallCounter(limits.twelve_data_usage_file, limits.twelve_data_daily_ceiling)
    return VendorGate(
        "Twelve Data", SlidingWindowRateLimiter(8, 60.0, clock=clock, sleep=sleep),
        attempts=TWELVE_DATA_ATTEMPTS, base_delay=2.0, max_delay=65.0,
        budget=TWELVE_DATA_WAIT_BUDGET_SEC, clock=clock, sleep=sleep, daily=daily,
    )


YAHOO = "yahoo"
TWELVE_DATA = "twelve_data"
_GATE_FACTORIES: Dict[str, Callable[..., VendorGate]] = {
    YAHOO: create_yahoo_gate,
    TWELVE_DATA: create_twelve_data_gate,
}
_gates: Dict[str, VendorGate] = {}
_gates_lock = Lock()


def vendor_gate(vendor: str) -> VendorGate:
    """The process's one gate for `vendor` (YAHOO or TWELVE_DATA), made on first use."""
    with _gates_lock:
        gate = _gates.get(vendor)
        if gate is None:
            gate = _gates[vendor] = _GATE_FACTORIES[vendor]()
        return gate
