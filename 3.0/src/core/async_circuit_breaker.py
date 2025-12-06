"""
Async Circuit Breaker for async/await code paths.

Adapted from sync version for use with aiohttp and other async clients.
"""

import asyncio
import logging
import time
from typing import Callable, Optional, Any, TypeVar, Coroutine
import functools

from .circuit_breaker import CircuitState, CircuitBreakerOpenError

logger = logging.getLogger(__name__)

T = TypeVar('T')

__all__ = [
    'AsyncCircuitBreaker',
]


class AsyncCircuitBreaker:
    """
    Async circuit breaker for protecting against cascading API failures.

    Same state machine as sync version but uses asyncio.Lock for thread safety.

    Example:
        breaker = AsyncCircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60,
            name="async_tradier"
        )

        @breaker.protect
        async def fetch_data(ticker):
            return await api.get(ticker)
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60,
        success_threshold: int = 2,
        expected_exception: type = Exception,
        name: str = "default"
    ) -> None:
        """
        Initialize async circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            success_threshold: Successes needed in half-open to close
            expected_exception: Exception type to catch
            name: Name for this circuit breaker (for logging)
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.success_threshold = success_threshold
        self.expected_exception = expected_exception
        self.name = name

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: Optional[float] = None
        self._lock = asyncio.Lock()

        logger.info(
            f"Initialized async circuit breaker '{name}': "
            f"threshold={failure_threshold}, timeout={recovery_timeout}s"
        )

    @property
    def state(self) -> CircuitState:
        """Get current circuit state (not thread-safe for reads)."""
        return self._state

    @property
    def failure_count(self) -> int:
        """Get current failure count."""
        return self._failure_count

    async def is_open(self) -> bool:
        """Check if circuit is currently open."""
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if self._last_failure_time:
                    elapsed = time.time() - self._last_failure_time
                    if elapsed >= self.recovery_timeout:
                        self._state = CircuitState.HALF_OPEN
                        self._success_count = 0
                        logger.info(f"Circuit '{self.name}': OPEN -> HALF_OPEN")
                        return False
                return True
            return False

    async def call(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any
    ) -> T:
        """
        Execute async function with circuit breaker protection.

        Args:
            func: Async function to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            CircuitBreakerOpenError: If circuit is open
            Exception: If function raises and circuit trips
        """
        async with self._lock:
            if self._state == CircuitState.OPEN:
                if self._last_failure_time:
                    elapsed = time.time() - self._last_failure_time
                    if elapsed >= self.recovery_timeout:
                        self._state = CircuitState.HALF_OPEN
                        self._success_count = 0
                        logger.info(f"Circuit '{self.name}': OPEN -> HALF_OPEN")
                    else:
                        raise CircuitBreakerOpenError(
                            f"Circuit '{self.name}' is OPEN. "
                            f"Retry in {self.recovery_timeout - elapsed:.1f}s"
                        )

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except self.expected_exception as e:
            await self._on_failure()
            raise

    async def _on_success(self) -> None:
        """Handle successful call."""
        async with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info(f"Circuit '{self.name}': HALF_OPEN -> CLOSED (recovered)")
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    async def _on_failure(self) -> None:
        """Handle failed call."""
        async with self._lock:
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning(f"Circuit '{self.name}': HALF_OPEN -> OPEN (recovery failed)")
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self.failure_threshold:
                    self._state = CircuitState.OPEN
                    logger.warning(
                        f"Circuit '{self.name}': CLOSED -> OPEN "
                        f"(threshold reached: {self._failure_count})"
                    )

    async def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        async with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
            logger.info(f"Circuit '{self.name}': Manually reset to CLOSED")

    def protect(
        self, func: Callable[..., Coroutine[Any, Any, T]]
    ) -> Callable[..., Coroutine[Any, Any, T]]:
        """
        Decorator to protect an async function with this circuit breaker.

        Example:
            @breaker.protect
            async def risky_call():
                return await external_api.get()
        """
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> T:
            return await self.call(func, *args, **kwargs)
        return wrapper

    def __repr__(self) -> str:
        return (
            f"AsyncCircuitBreaker(name='{self.name}', state={self._state.value}, "
            f"failures={self._failure_count}/{self.failure_threshold})"
        )
