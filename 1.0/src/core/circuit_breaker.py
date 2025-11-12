"""
Circuit Breaker pattern for API fault tolerance.

Prevents cascading failures by "opening" the circuit after repeated failures,
allowing the system to fail fast and recover gracefully.
"""

import logging
import threading
import time
from enum import Enum
from typing import Callable, Optional, Any, TypeVar
import functools

logger = logging.getLogger(__name__)

T = TypeVar('T')


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"      # Normal operation, requests pass through
    OPEN = "open"          # Circuit is open, requests fail immediately
    HALF_OPEN = "half_open"  # Testing if service has recovered


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
        expected_exception: type = Exception,
        name: str = "default"
    ) -> None:
        """
        Initialize circuit breaker.

        Args:
            failure_threshold: Number of failures before opening circuit
            recovery_timeout: Seconds to wait before attempting recovery (half-open)
            expected_exception: Exception type to catch (default: Exception)
            name: Name for this circuit breaker (for logging)
        """
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        self.name = name

        self._state = CircuitState.CLOSED
        self._failure_count = 0
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
            Exception: If function raises an error
        """
        with self._lock:
            if self._state == CircuitState.OPEN:
                # Check if recovery timeout has elapsed
                if self._should_attempt_reset():
                    logger.info(f"{self.name}: Attempting recovery (HALF_OPEN)")
                    self._state = CircuitState.HALF_OPEN
                else:
                    raise CircuitBreakerOpenError(
                        f"Circuit breaker '{self.name}' is OPEN. "
                        f"Will retry in {self._time_until_reset():.1f}s"
                    )

        # Execute the function
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise

    def protect(self, func: Callable[..., T]) -> Callable[..., T]:
        """
        Decorator to protect a function with circuit breaker.

        Example:
            @breaker.protect
            def api_call():
                return requests.get(...)
        """
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            return self.call(func, *args, **kwargs)
        return wrapper

    def _on_success(self) -> None:
        """Handle successful function call."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                logger.info(f"{self.name}: Service recovered, closing circuit")
                self._state = CircuitState.CLOSED

            self._failure_count = 0
            self._last_failure_time = None

    def _on_failure(self) -> None:
        """Handle failed function call."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            if self._state == CircuitState.HALF_OPEN:
                # Failed during recovery attempt
                logger.warning(f"{self.name}: Recovery failed, opening circuit again")
                self._state = CircuitState.OPEN
            elif self._failure_count >= self.failure_threshold:
                # Too many failures, open the circuit
                logger.error(
                    f"{self.name}: Failure threshold reached "
                    f"({self._failure_count}/{self.failure_threshold}), "
                    f"opening circuit"
                )
                self._state = CircuitState.OPEN

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has elapsed to attempt recovery."""
        if self._last_failure_time is None:
            return True

        elapsed = time.time() - self._last_failure_time
        return elapsed >= self.recovery_timeout

    def _time_until_reset(self) -> float:
        """Calculate time remaining until recovery attempt."""
        if self._last_failure_time is None:
            return 0.0

        elapsed = time.time() - self._last_failure_time
        return max(0.0, self.recovery_timeout - elapsed)

    def reset(self) -> None:
        """Manually reset the circuit breaker to CLOSED state."""
        with self._lock:
            logger.info(f"{self.name}: Circuit manually reset")
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._last_failure_time = None

    def get_stats(self) -> dict:
        """Get current circuit breaker statistics."""
        with self._lock:
            return {
                'name': self.name,
                'state': self._state.value,
                'failure_count': self._failure_count,
                'failure_threshold': self.failure_threshold,
                'time_until_reset': self._time_until_reset() if self._state == CircuitState.OPEN else 0
            }


class CircuitBreakerManager:
    """
    Manage multiple circuit breakers for different services.

    Example:
        manager = CircuitBreakerManager()
        manager.add_breaker('tradier', failure_threshold=5, recovery_timeout=60)
        manager.add_breaker('yfinance', failure_threshold=3, recovery_timeout=30)

        @manager.protect('tradier')
        def fetch_tradier_data():
            ...
    """

    def __init__(self) -> None:
        """Initialize circuit breaker manager."""
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = threading.Lock()

    def add_breaker(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60,
        expected_exception: type = Exception
    ) -> CircuitBreaker:
        """
        Add a circuit breaker for a specific service.

        Args:
            name: Service name
            failure_threshold: Failures before opening
            recovery_timeout: Seconds before retry
            expected_exception: Exception type to catch

        Returns:
            Created circuit breaker
        """
        with self._lock:
            if name in self._breakers:
                logger.warning(f"Overwriting existing circuit breaker: {name}")

            breaker = CircuitBreaker(
                failure_threshold=failure_threshold,
                recovery_timeout=recovery_timeout,
                expected_exception=expected_exception,
                name=name
            )
            self._breakers[name] = breaker
            return breaker

    def get_breaker(self, name: str) -> CircuitBreaker:
        """Get a circuit breaker by name."""
        if name not in self._breakers:
            raise KeyError(f"No circuit breaker configured for '{name}'")
        return self._breakers[name]

    def protect(self, name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
        """
        Decorator to protect a function with a named circuit breaker.

        Args:
            name: Circuit breaker name

        Example:
            @manager.protect('api_service')
            def api_call():
                ...
        """
        def decorator(func: Callable[..., T]) -> Callable[..., T]:
            breaker = self.get_breaker(name)
            return breaker.protect(func)
        return decorator

    def reset_all(self) -> None:
        """Reset all circuit breakers."""
        with self._lock:
            for breaker in self._breakers.values():
                breaker.reset()
            logger.info("All circuit breakers reset")

    def get_all_stats(self) -> dict[str, dict]:
        """Get statistics for all circuit breakers."""
        return {
            name: breaker.get_stats()
            for name, breaker in self._breakers.items()
        }


# Global circuit breaker manager for convenience
_global_breakers = CircuitBreakerManager()


def configure_circuit_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60,
    expected_exception: type = Exception
) -> CircuitBreaker:
    """
    Configure a global circuit breaker for a service.

    Args:
        name: Service name
        failure_threshold: Failures before opening
        recovery_timeout: Seconds before retry
        expected_exception: Exception type to catch

    Returns:
        Configured circuit breaker
    """
    return _global_breakers.add_breaker(
        name,
        failure_threshold,
        recovery_timeout,
        expected_exception
    )


def protect(name: str) -> Callable[[Callable[..., T]], Callable[..., T]]:
    """
    Decorator to protect a function with a global circuit breaker.

    Args:
        name: Circuit breaker name

    Example:
        @protect('external_api')
        def fetch_data():
            ...
    """
    return _global_breakers.protect(name)


def get_breaker_stats() -> dict[str, dict]:
    """Get statistics for all configured circuit breakers."""
    return _global_breakers.get_all_stats()
