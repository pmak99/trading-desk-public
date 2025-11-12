"""Circuit breaker pattern for API resilience."""

import logging
import time
from datetime import datetime, timedelta
from enum import Enum
from threading import Lock
from typing import Callable, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is in OPEN state."""
    pass


class CircuitBreaker:
    """Circuit breaker for API resilience.

    Protects external API calls by tracking failures and temporarily blocking
    requests when failure threshold is exceeded.

    States:
        CLOSED: Normal operation, requests pass through
        OPEN: Too many failures, requests are blocked
        HALF_OPEN: Testing if service has recovered

    Args:
        name: Identifier for this circuit breaker
        failure_threshold: Number of failures before opening circuit
        recovery_timeout: Seconds to wait before attempting recovery
    """

    def __init__(self, name: str, failure_threshold=5, recovery_timeout=60):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CircuitState.CLOSED
        self.failure_count = 0
        self.last_failure_time = None
        self._lock = Lock()  # Thread safety for state mutations

    def call(self, func: Callable, *args, **kwargs):
        """Execute function with circuit breaker protection.

        Args:
            func: Function to call
            *args: Positional arguments for func
            **kwargs: Keyword arguments for func

        Returns:
            Result from func

        Raises:
            CircuitBreakerOpenError: If circuit is OPEN
            Exception: Any exception from func
        """

        with self._lock:
            if self.state == CircuitState.OPEN:
                if self._should_attempt_reset():
                    self.state = CircuitState.HALF_OPEN
                    logger.info(f"Circuit {self.name} entering HALF_OPEN")
                else:
                    raise CircuitBreakerOpenError(f"Circuit {self.name} is OPEN")

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception as e:
            self._on_failure()
            raise

    def _on_success(self):
        """Handle successful call."""
        with self._lock:
            if self.state == CircuitState.HALF_OPEN:
                logger.info(f"Circuit {self.name} recovered")
            self.state = CircuitState.CLOSED
            self.failure_count = 0
            self.last_failure_time = None

    def _on_failure(self):
        """Handle failed call."""
        with self._lock:
            self.failure_count += 1
            self.last_failure_time = datetime.now()

            if self.failure_count >= self.failure_threshold:
                logger.error(f"Circuit {self.name} opening after {self.failure_count} failures")
                self.state = CircuitState.OPEN

    def _should_attempt_reset(self) -> bool:
        """Check if enough time has passed to attempt recovery."""
        if not self.last_failure_time:
            return True
        elapsed = datetime.now() - self.last_failure_time
        return elapsed >= timedelta(seconds=self.recovery_timeout)
