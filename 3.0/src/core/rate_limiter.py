"""
Token Bucket Rate Limiter for API rate limiting.

Ported from 1.0 - implements token bucket algorithm for smooth rate limiting
with burst capacity support.
"""

import asyncio
import logging
import threading
import time
from typing import Dict, Optional

logger = logging.getLogger(__name__)

__all__ = [
    'TokenBucketRateLimiter',
    'AsyncTokenBucketRateLimiter',
    'MultiRateLimiter',
]


class TokenBucketRateLimiter:
    """
    Token bucket rate limiter for sync code.

    Allows bursts while maintaining average rate over time.

    Example:
        limiter = TokenBucketRateLimiter(
            rate=2.0,        # 2 requests per second
            capacity=10      # Allow burst of 10 requests
        )

        for request in requests:
            limiter.acquire()  # Blocks until token available
            make_request(request)
    """

    def __init__(
        self,
        rate: float,
        capacity: Optional[int] = None,
        name: str = "default"
    ) -> None:
        """
        Initialize token bucket rate limiter.

        Args:
            rate: Tokens added per second (requests per second)
            capacity: Maximum bucket capacity (default: rate * 2)
            name: Name for logging
        """
        self.rate = rate
        self.capacity = capacity or int(rate * 2)
        self.name = name

        self._tokens = float(self.capacity)
        self._last_update = time.time()
        self._lock = threading.Lock()

        logger.debug(
            f"Rate limiter '{name}': rate={rate}/s, capacity={self.capacity}"
        )

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self._last_update
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_update = now

    def acquire(self, tokens: int = 1, blocking: bool = True) -> bool:
        """
        Acquire tokens from the bucket.

        Args:
            tokens: Number of tokens to acquire (default: 1)
            blocking: If True, block until tokens available

        Returns:
            True if tokens acquired, False if non-blocking and unavailable
        """
        with self._lock:
            self._refill()

            if self._tokens >= tokens:
                self._tokens -= tokens
                return True

            if not blocking:
                return False

        # Wait for tokens (outside lock)
        while True:
            time.sleep(0.1)
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True

    def available(self) -> float:
        """Get current number of available tokens."""
        with self._lock:
            self._refill()
            return self._tokens

    def wait_time(self, tokens: int = 1) -> float:
        """
        Get estimated wait time for tokens.

        Args:
            tokens: Number of tokens needed

        Returns:
            Seconds to wait (0 if tokens available)
        """
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                return 0.0
            needed = tokens - self._tokens
            return needed / self.rate


class AsyncTokenBucketRateLimiter:
    """
    Async token bucket rate limiter for async code.

    Same algorithm as sync version but uses asyncio primitives.
    """

    def __init__(
        self,
        rate: float,
        capacity: Optional[int] = None,
        name: str = "default"
    ) -> None:
        """
        Initialize async token bucket rate limiter.

        Args:
            rate: Tokens added per second
            capacity: Maximum bucket capacity
            name: Name for logging
        """
        self.rate = rate
        self.capacity = capacity or int(rate * 2)
        self.name = name

        self._tokens = float(self.capacity)
        self._last_update = time.time()
        self._lock = asyncio.Lock()

        logger.debug(
            f"Async rate limiter '{name}': rate={rate}/s, capacity={self.capacity}"
        )

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.time()
        elapsed = now - self._last_update
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
        self._last_update = now

    async def acquire(self, tokens: int = 1) -> None:
        """
        Acquire tokens from the bucket (async).

        Args:
            tokens: Number of tokens to acquire
        """
        while True:
            async with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return

            # Calculate wait time and sleep
            wait_time = (tokens - self._tokens) / self.rate
            await asyncio.sleep(min(wait_time, 0.1))

    async def try_acquire(self, tokens: int = 1) -> bool:
        """
        Try to acquire tokens without blocking.

        Args:
            tokens: Number of tokens to acquire

        Returns:
            True if acquired, False otherwise
        """
        async with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False


class MultiRateLimiter:
    """
    Manage multiple rate limiters for different services.

    Example:
        limiters = MultiRateLimiter()
        limiters.add("tradier", rate=2.0, capacity=10)
        limiters.add("yahoo", rate=1.0, capacity=5)

        limiters.acquire("tradier")
        tradier_api.call()
    """

    def __init__(self) -> None:
        """Initialize multi-rate limiter."""
        self._limiters: Dict[str, TokenBucketRateLimiter] = {}
        self._async_limiters: Dict[str, AsyncTokenBucketRateLimiter] = {}

    def add(
        self,
        name: str,
        rate: float,
        capacity: Optional[int] = None
    ) -> None:
        """
        Add a new rate limiter.

        Args:
            name: Service name
            rate: Requests per second
            capacity: Burst capacity
        """
        self._limiters[name] = TokenBucketRateLimiter(
            rate=rate, capacity=capacity, name=name
        )
        self._async_limiters[name] = AsyncTokenBucketRateLimiter(
            rate=rate, capacity=capacity, name=name
        )
        logger.info(f"Added rate limiter: {name} ({rate}/s, burst={capacity or int(rate*2)})")

    def acquire(self, name: str, tokens: int = 1, blocking: bool = True) -> bool:
        """
        Acquire tokens from a specific limiter.

        Args:
            name: Service name
            tokens: Tokens to acquire
            blocking: Block until available

        Returns:
            True if acquired
        """
        if name not in self._limiters:
            logger.warning(f"Unknown rate limiter: {name}")
            return True

        return self._limiters[name].acquire(tokens, blocking)

    async def acquire_async(self, name: str, tokens: int = 1) -> None:
        """
        Acquire tokens from a specific limiter (async).

        Args:
            name: Service name
            tokens: Tokens to acquire
        """
        if name not in self._async_limiters:
            logger.warning(f"Unknown async rate limiter: {name}")
            return

        await self._async_limiters[name].acquire(tokens)

    def get_stats(self) -> Dict[str, Dict[str, float]]:
        """Get statistics for all limiters."""
        stats = {}
        for name, limiter in self._limiters.items():
            stats[name] = {
                'available': limiter.available(),
                'rate': limiter.rate,
                'capacity': limiter.capacity,
            }
        return stats


# Default rate limits for common services
DEFAULT_RATE_LIMITS = {
    'tradier': {'rate': 2.0, 'capacity': 10},    # 120/min
    'yahoo': {'rate': 1.0, 'capacity': 5},        # 60/min
    'alpha_vantage': {'rate': 0.2, 'capacity': 5}, # 5/min (free tier)
}


def create_default_limiters() -> MultiRateLimiter:
    """Create rate limiters with default service limits."""
    limiters = MultiRateLimiter()
    for name, config in DEFAULT_RATE_LIMITS.items():
        limiters.add(name, **config)
    return limiters
