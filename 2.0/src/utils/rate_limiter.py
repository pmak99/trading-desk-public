"""
Rate limiter using token bucket algorithm.

Prevents exceeding API rate limits by controlling request frequency.
"""

import time
import logging
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)


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


class CompositRateLimiter:
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


def create_alpha_vantage_limiter() -> CompositeRateLimiter:
    """
    Create rate limiter for Alpha Vantage API.

    Alpha Vantage free tier limits:
    - 5 calls per minute
    - 500 calls per day
    """
    return CompositeRateLimiter(
        [
            TokenBucketRateLimiter(rate=5, per_seconds=60),  # 5/min
            TokenBucketRateLimiter(rate=500, per_seconds=86400),  # 500/day
        ]
    )


def create_tradier_limiter() -> TokenBucketRateLimiter:
    """
    Create rate limiter for Tradier API.

    Tradier is more permissive: ~120 calls/minute typical.
    """
    return TokenBucketRateLimiter(rate=120, per_seconds=60, burst=150)
