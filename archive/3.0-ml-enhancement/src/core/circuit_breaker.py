"""
Circuit Breaker pattern for API fault tolerance.

Ported from 1.0 - prevents cascading failures by "opening" the circuit
after repeated failures, allowing the system to fail fast and recover gracefully.
"""

import logging
import threading
import time
from enum import Enum
from typing import Callable, Optional, Any, TypeVar
import functools

logger = logging.getLogger(__name__)

T = TypeVar('T')

__all__ = [
    'CircuitState',
    'CircuitBreakerOpenError',
    'CircuitBreaker',
]


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"       # Normal operation, requests pass through
    OPEN = "open"           # Circuit is open, requests fail immediately
    HALF_OPEN = "half_open" # Testing if service has recovered


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is open."""
    pass


class CircuitBreaker:
    """
    Circuit breaker for protecting against cascading API failures.

    States:
        - CLOSED: Normal operation, all requests pass through
        - OPEN: Too many failures, fail fast without calling service
        - HALF_OPEN: Allow one request to test if service recovered

    Example:
        breaker = CircuitBreaker(
            failure_threshold=5,
            recovery_timeout=60,
            name="tradier_api"
        )

        @breaker.protect
        def fetch_data(ticker):
            return api.get(ticker)
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
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery
            success_threshold: Successes needed in half-open to close
            expected_exception: Exception type to catch (default: Exception)
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
        self._lock = threading.Lock()

        logger.info(
            f"Initialized circuit breaker '{name}': "
            f"threshold={failure_threshold}, timeout={recovery_timeout}s"
        )

    @property
    def state(self) -> CircuitState:
        """Get current circuit state."""
        with self._lock:
            return self._state

    @property
    def failure_count(self) -> int:
        """Get current failure count."""
        with self._lock:
            return self._failure_count

    def is_open(self) -> bool:
        """Check if circuit is currently open."""
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if we should transition to half-open
                if self._last_failure_time:
                    elapsed = time.time() - self._last_failure_time
                    if elapsed >= self.recovery_timeout:
                        self._state = CircuitState.HALF_OPEN
                        self._success_count = 0
                        logger.info(f"Circuit '{self.name}': OPEN -> HALF_OPEN")
                        return False
                return True
            return False

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        """
        Execute function with circuit breaker protection.

        Args:
            func: Function to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Function result

        Raises:
            CircuitBreakerOpenError: If circuit is open
            Exception: If function raises and circuit trips
        """
        with self._lock:
            # Check state and potentially transition
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
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        """Handle successful call."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self.success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    logger.info(f"Circuit '{self.name}': HALF_OPEN -> CLOSED (recovered)")
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    def _on_failure(self) -> None:
        """Handle failed call."""
        with self._lock:
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

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._last_failure_time = None
            logger.info(f"Circuit '{self.name}': Manually reset to CLOSED")

    def protect(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Decorator to protect a function with this circuit breaker.

        Example:
            @breaker.protect
            def risky_call():
                return external_api.get()
        """
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return self.call(func, *args, **kwargs)
        return wrapper

    def __repr__(self) -> str:
        return (
            f"CircuitBreaker(name='{self.name}', state={self._state.value}, "
            f"failures={self._failure_count}/{self.failure_threshold})"
        )
