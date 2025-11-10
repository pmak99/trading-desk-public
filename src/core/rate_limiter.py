"""
Token bucket rate limiter for API call throttling.

Implements a token bucket algorithm to control the rate of API requests
and prevent hitting rate limits from external services.
"""

import logging
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)


class TokenBucketRateLimiter:
    """
    Token bucket rate limiter for controlling API request rates.

    The token bucket algorithm allows bursts of requests up to the bucket
    capacity while maintaining an average rate over time.

    Example:
        # Allow 10 requests per second with burst up to 20
        limiter = TokenBucketRateLimiter(rate=10, capacity=20)

        for ticker in tickers:
            limiter.acquire()  # Blocks if no tokens available
            fetch_data(ticker)
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
            capacity: Maximum bucket size (default: 2x rate for burst)
            name: Name for this rate limiter (for logging)
        """
        self.rate = rate
        self.capacity = capacity or int(rate * 2)
        self.name = name

        self._tokens = float(self.capacity)
        self._last_refill = time.time()
        self._lock = threading.Lock()

        logger.info(
            f"Initialized {name} rate limiter: {rate} req/sec, "
            f"burst capacity: {self.capacity}"
        )

    def acquire(self, tokens: int = 1, blocking: bool = True) -> bool:
        """
        Acquire tokens from the bucket.

        Args:
            tokens: Number of tokens to acquire (default: 1)
            blocking: If True, wait until tokens available. If False, return immediately.

        Returns:
            True if tokens acquired, False if not available (non-blocking mode only)
        """
        while True:
            with self._lock:
                self._refill()

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True

                if not blocking:
                    return False

                # Calculate wait time for next token
                tokens_needed = tokens - self._tokens
                wait_time = tokens_needed / self.rate

            # Release lock while waiting
            logger.debug(
                f"{self.name}: Waiting {wait_time:.2f}s for {tokens} token(s)"
            )
            time.sleep(wait_time)

    def _refill(self) -> None:
        """Refill tokens based on elapsed time since last refill."""
        now = time.time()
        elapsed = now - self._last_refill

        # Add tokens based on elapsed time
        tokens_to_add = elapsed * self.rate
        self._tokens = min(self.capacity, self._tokens + tokens_to_add)
        self._last_refill = now

    def get_available_tokens(self) -> float:
        """Get current number of available tokens."""
        with self._lock:
            self._refill()
            return self._tokens

    def reset(self) -> None:
        """Reset the bucket to full capacity."""
        with self._lock:
            self._tokens = float(self.capacity)
            self._last_refill = time.time()
            logger.info(f"{self.name}: Rate limiter reset")


class MultiRateLimiter:
    """
    Manage multiple rate limiters for different API providers.

    Allows different rate limits for different services (e.g., Tradier, yfinance).

    Example:
        limiters = MultiRateLimiter()
        limiters.add_limiter('tradier', rate=120, capacity=200)  # 120/min
        limiters.add_limiter('yfinance', rate=5, capacity=10)    # 5/sec

        limiters.acquire('tradier')
        fetch_tradier_data()
    """

    def __init__(self) -> None:
        """Initialize multi-rate limiter manager."""
        self._limiters: dict[str, TokenBucketRateLimiter] = {}
        self._lock = threading.Lock()

    def add_limiter(
        self,
        name: str,
        rate: float,
        capacity: Optional[int] = None
    ) -> None:
        """
        Add a rate limiter for a specific service.

        Args:
            name: Service name (e.g., 'tradier', 'yfinance')
            rate: Requests per second
            capacity: Maximum burst size
        """
        with self._lock:
            if name in self._limiters:
                logger.warning(f"Overwriting existing rate limiter: {name}")

            self._limiters[name] = TokenBucketRateLimiter(
                rate=rate,
                capacity=capacity,
                name=name
            )

    def acquire(self, name: str, tokens: int = 1, blocking: bool = True) -> bool:
        """
        Acquire tokens from a specific rate limiter.

        Args:
            name: Service name
            tokens: Number of tokens to acquire
            blocking: Whether to block until tokens available

        Returns:
            True if tokens acquired, False otherwise

        Raises:
            KeyError: If rate limiter doesn't exist
        """
        if name not in self._limiters:
            raise KeyError(
                f"No rate limiter configured for '{name}'. "
                f"Available: {list(self._limiters.keys())}"
            )

        return self._limiters[name].acquire(tokens=tokens, blocking=blocking)

    def get_limiter(self, name: str) -> TokenBucketRateLimiter:
        """Get a specific rate limiter."""
        if name not in self._limiters:
            raise KeyError(f"No rate limiter configured for '{name}'")
        return self._limiters[name]

    def reset_all(self) -> None:
        """Reset all rate limiters."""
        with self._lock:
            for limiter in self._limiters.values():
                limiter.reset()
            logger.info("All rate limiters reset")

    def get_stats(self) -> dict[str, float]:
        """Get current token availability for all limiters."""
        return {
            name: limiter.get_available_tokens()
            for name, limiter in self._limiters.items()
        }


# Global rate limiter instance for convenience
_global_limiters = MultiRateLimiter()


def configure_rate_limiter(
    name: str,
    rate: float,
    capacity: Optional[int] = None
) -> None:
    """
    Configure a global rate limiter for a service.

    Args:
        name: Service name
        rate: Requests per second
        capacity: Maximum burst size
    """
    _global_limiters.add_limiter(name, rate, capacity)


def acquire_token(name: str, tokens: int = 1, blocking: bool = True) -> bool:
    """
    Acquire tokens from a global rate limiter.

    Args:
        name: Service name
        tokens: Number of tokens
        blocking: Whether to block

    Returns:
        True if acquired, False otherwise
    """
    return _global_limiters.acquire(name, tokens, blocking)


def get_rate_limiter_stats() -> dict[str, float]:
    """Get statistics for all configured rate limiters."""
    return _global_limiters.get_stats()
